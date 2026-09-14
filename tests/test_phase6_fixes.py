"""Regressions for what the Phase 6 eval suites found (evals/REPORT.md, before and after).

* one captain decision per resident (suite 2: a declined door-knock became a second decision);
* a case with a volunteer on the way is not RESOLVED until the volunteer replies (suite 2);
* "On my way" leaves the volunteer a way to report back;
* a volunteer brief never names a resident who did not consent, even through the model's reason;
* three unclear calls escalate as "could not confirm", not as an unmet need;
* protocol completion accepts a question asked aloud and answered, even when the model never
  called `record_answer` (suite 1: four OK calls became UNCLEAR that way).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_ctx
from doorstep_agent.agents.classifier import unanswered_required
from doorstep_agent.decisions import Responder, respond_to_decision, upsert_decision
from doorstep_agent.messages import VOLUNTEER_UPDATE
from doorstep_agent.models import (
    CaseState,
    CheckinAttempt,
    CheckinResult,
    CheckinStatus,
    ConversationTurn,
    DecisionOption,
)
from doorstep_agent.runtime import RunContext
from doorstep_agent.state_machine import is_settled
from doorstep_agent.tools import send_volunteer_task
from helpers.agent_harness import ToolCall, invoke, responses, run_script

DECLINE = {"option_id": "handle", "label": "I'm handling it", "action": "resolve"}


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return make_ctx(auto_approve=False, sessions_dir=tmp_path / "sessions")


def with_result(ctx: RunContext, rid: str, status: CheckinStatus, **kw) -> None:
    case = ctx.store.case(ctx.incident_id, rid)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=status, **kw))
    ctx.store.save_case(case)


def captain_decisions(ctx: RunContext, rid: str) -> list:
    return [
        d
        for d in ctx.store.decisions(ctx.incident_id)
        if d.resident_id == rid and d.audience == ctx.org.captain_id
    ]


async def test_a_declined_door_knock_is_not_followed_by_a_second_decision(ctx: RunContext) -> None:
    with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])  # wave 1: needs approval
    assign = ToolCall(
        "assign_volunteer",
        {"resident_id": "r04", "volunteer_id": "vol-tom", "include_brief": True, "reason": "ride"},
        "tu-assign",
    )
    model = run_script(assign, final_text="asked")
    paused = await invoke(ctx, "r04", "help r04", model=model)
    decision = ctx.store.decisions(ctx.incident_id, status="draft")[0]
    decision.status = "answered"
    decision.response = "handle"
    ctx.store.save_decision(decision)

    # The model then tries to escalate the same resident as an unmet need, as Nova did.
    again = run_script(
        ToolCall(
            "escalate_to_captain",
            {"resident_id": "r04", "reason": "nobody can drive", "options": []},
            "tu-escalate",
        ),
        final_text="escalated",
    )
    result = await invoke(ctx, "r04", responses(paused, DECLINE), model=again)

    assert result.stop_reason != "interrupt"
    assert len(captain_decisions(ctx, "r04")) == 1


async def test_a_pending_captain_decision_blocks_another(ctx: RunContext) -> None:
    with_result(ctx, "r01", CheckinStatus.URGENT, red_flags=["confusion"])
    earlier = upsert_decision(
        ctx,
        tool_use_id="earlier",
        resident_id="r01",
        name="doorstep-urgent-red-flag",
        reason="confused",
        options=[DecisionOption(id="handle", label="I'm handling it", action="resolve")],
        audience=ctx.org.captain_id,
    )
    earlier.status = "pending"
    ctx.store.save_decision(earlier)
    model = run_script(
        ToolCall(
            "escalate_to_captain", {"resident_id": "r01", "reason": "still", "options": []}, "b"
        )
    )
    result = await invoke(ctx, "r01", "again", model=model)
    assert result.stop_reason != "interrupt"
    assert len(captain_decisions(ctx, "r01")) == 1


async def test_close_case_waits_for_the_volunteer_and_their_reply_closes_it(
    ctx: RunContext,
) -> None:
    with_result(ctx, "r10", CheckinStatus.NEEDS_HELP, needs=["ride"])  # wave 2: no approval
    model = run_script(
        ToolCall(
            "assign_volunteer",
            {"resident_id": "r10", "volunteer_id": "vol-tom", "include_brief": True, "reason": "x"},
            "tu-a",
        ),
        ToolCall(
            "close_case", {"resident_id": "r10", "outcome": "helped", "reason": "sent"}, "tu-c"
        ),
    )
    await invoke(ctx, "r10", "help", model=model)

    case = ctx.store.case(ctx.incident_id, "r10")
    assert case.state == CaseState.ASSIGNED
    assert is_settled(case), "an assigned case is parked on a human, so a drill can finish"
    task = next(d for d in ctx.store.decisions(ctx.incident_id) if d.name == VOLUNTEER_UPDATE)

    who = Responder(source="drill", external_id="test")
    await respond_to_decision(ctx, task.id, "otw", who, actor_override="volunteer:vol-tom")
    assert ctx.store.case(ctx.incident_id, "r10").state == CaseState.ASSIGNED
    follow = [
        d
        for d in ctx.store.decisions(ctx.incident_id, status="pending")
        if d.name == VOLUNTEER_UPDATE
    ]
    assert [o.id for o in follow[0].options] == ["ok", "more"]

    await respond_to_decision(ctx, follow[0].id, "ok", who, actor_override="volunteer:vol-tom")
    assert ctx.store.case(ctx.incident_id, "r10").state == CaseState.RESOLVED


def test_a_brief_never_names_a_resident_without_sharing_consent(ctx: RunContext) -> None:
    r = ctx.store.resident("r08")  # share_with_volunteer: false
    assert not r.consent.share_with_volunteer
    with_result(ctx, "r08", CheckinStatus.NEEDS_HELP, needs=["water"])
    send_volunteer_task(
        ctx,
        r,
        ctx.store.volunteer("vol-tom"),
        f"{r.first_name} needs water; {r.name} asked for a visit",
        include_brief=True,
        tool_use_id="tu",
    )
    text = next(m.text for m in ctx.outbox if m.kind == "volunteer_task")
    assert r.first_name not in text


async def test_three_unclear_calls_escalate_as_no_answer_not_unmet_need(ctx: RunContext) -> None:
    case = ctx.store.case(ctx.incident_id, "r11")
    for n in range(3):
        if n:
            ctx.policy.requeue(case)
        ctx.policy.start_attempt(case, "simulated")
        ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.UNCLEAR))
    ctx.store.save_case(case)
    assert case.state == CaseState.ESCALATED
    model = run_script(
        ToolCall("escalate_to_captain", {"resident_id": "r11", "reason": "?", "options": []}, "u")
    )
    await invoke(ctx, "r11", "unclear", model=model)
    assert [d.name for d in captain_decisions(ctx, "r11")] == ["doorstep-high-risk-no-answer"]


def _attempt(*turns: tuple[str, str]) -> CheckinAttempt:
    return CheckinAttempt(
        attempt=1,
        channel="simulated",
        transcript=[ConversationTurn(speaker=s, text=t) for s, t in turns],
    )


def test_protocol_completion_counts_a_question_asked_aloud_and_answered(ctx: RunContext) -> None:
    asked = _attempt(
        ("resident", "Hello?"),
        ("agent", "Great. How are you feeling right now?"),
        ("resident", "Cool enough. I'm in the basement."),
    )
    assert unanswered_required(ctx, asked) == []

    spanish = _attempt(("agent", "Ahora, ¿cómo se siente ahora mismo?"), ("resident", "Bien."))
    assert unanswered_required(ctx, spanish) == []


def test_protocol_completion_still_misses_an_unasked_or_unanswered_question(
    ctx: RunContext,
) -> None:
    never_asked = _attempt(("agent", "Is it cool where you are?"), ("resident", "Yes."))
    assert unanswered_required(ctx, never_asked) == ["feeling"]
    hung_up = _attempt(("agent", "How are you feeling right now?"), ("resident", "[hung up]"))
    assert unanswered_required(ctx, hung_up) == ["feeling"]


def test_a_recorded_answer_to_a_question_never_asked_does_not_count(ctx: RunContext) -> None:
    fabricated = _attempt(
        ("resident", "Hello?"),
        ("agent", "Hi, is this Ruth? This is Doorstep. Is now OK for a quick minute?"),
        ("resident", "Oh, hello. Yes, this is Ruth."),
        ("agent", "Keep drinking water. We'll check in again this evening."),
        ("resident", "Thank you."),
    )
    fabricated.answers = {"feeling": "fine"}
    assert unanswered_required(ctx, fabricated) == ["feeling"]

    paraphrased = _attempt(("agent", "And how are you feeling today?"), ("resident", "Not bad."))
    paraphrased.answers = {"feeling": "not bad"}
    assert unanswered_required(ctx, paraphrased) == []
