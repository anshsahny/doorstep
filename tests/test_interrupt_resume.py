"""Interrupts and resume (Gate 2): the agent pauses for a human and continues correctly.

These drive the real dispatcher — real tools, real Cedar, real hooks, real session manager —
with only the model provider scripted, so what is under test is the SDK's interrupt machinery
and Doorstep's wiring of it, not a stand-in for either.

The two shapes, which behave differently on purpose (Spike A):

* `escalate_to_captain` interrupts from inside the tool, so its body re-runs on resume. Its
  pre-interrupt work must therefore be idempotent, and these tests prove it is.
* `assign_volunteer` is interrupted by `ApprovalHook` at admission, so its body does not run at
  all until the captain approves — the property that keeps a resident's details from reaching a
  volunteer's phone before anyone said yes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_ctx
from doorstep_agent.models import CaseState, CheckinResult, CheckinStatus
from doorstep_agent.runtime import RunContext
from helpers.agent_harness import ToolCall, invoke, responses, run_script

APPROVE = {"option_id": "approve", "label": "Send Tom (0.4 km)", "action": "assign_volunteer"}
DECLINE = {"option_id": "handle", "label": "I'm handling it", "action": "resolve"}


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return make_ctx(auto_approve=False, sessions_dir=tmp_path / "sessions")


def with_result(ctx: RunContext, rid: str, status: CheckinStatus, **kw) -> None:
    case = ctx.store.case(ctx.incident_id, rid)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=status, **kw))


def tasks(ctx: RunContext) -> list[str]:
    return [m.text for m in ctx.outbox if m.kind == "volunteer_task"]


# --- assign_volunteer: the hook stops the tool before it can send anything -----------------


async def test_high_risk_assignment_sends_nothing_until_the_captain_approves(
    ctx: RunContext,
) -> None:
    with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])  # r04 is wave 1
    model = run_script(
        ToolCall("assign_volunteer", _assign_input("r04"), "tu-assign-1"),
        final_text="assigned",
    )

    result = await invoke(ctx, "r04", "help r04", model=model)

    assert result.stop_reason == "interrupt"
    assert result.interrupts[0].name == "doorstep-approve-door-knock"
    assert tasks(ctx) == [], "the volunteer task must not exist before approval"
    # Still a draft: raised, but not answerable until the runner stamps the interrupt on it, so
    # no channel can put a button in front of a human yet.
    assert ctx.store.decisions(ctx.incident_id, status="pending") == []
    decision = ctx.store.decisions(ctx.incident_id, status="draft")[0]
    assert decision.audience == ctx.org.captain_id
    assert decision.interrupt_id is None
    assert ctx.store.case(ctx.incident_id, "r04").assigned_volunteer is None


async def test_approving_runs_the_tool_exactly_once(ctx: RunContext) -> None:
    with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])
    model = run_script(
        ToolCall("assign_volunteer", _assign_input("r04"), "tu-assign-2"), final_text="assigned"
    )
    paused = await invoke(ctx, "r04", "help r04", model=model)

    result = await invoke(ctx, "r04", responses(paused, APPROVE), model=model)

    assert result.stop_reason == "end_turn"
    assert len(tasks(ctx)) == 1, "approving must send the task once, not twice"
    case = ctx.store.case(ctx.incident_id, "r04")
    assert case.state == CaseState.ASSIGNED and case.assigned_volunteer == "vol-tom"


async def test_declining_never_runs_the_tool(ctx: RunContext) -> None:
    with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])
    model = run_script(
        ToolCall("assign_volunteer", _assign_input("r04"), "tu-assign-3"), final_text="stood down"
    )
    paused = await invoke(ctx, "r04", "help r04", model=model)

    await invoke(ctx, "r04", responses(paused, DECLINE), model=model)

    assert tasks(ctx) == []
    case = ctx.store.case(ctx.incident_id, "r04")
    assert case.assigned_volunteer is None and case.state == CaseState.ESCALATED


async def test_a_low_risk_assignment_is_never_interrupted(ctx: RunContext) -> None:
    with_result(ctx, "r11", CheckinStatus.NEEDS_HELP, needs=["water"])  # r11 is wave 3
    model = run_script(
        ToolCall(
            "assign_volunteer",
            {
                "resident_id": "r11",
                "volunteer_id": "vol-priya",
                "include_brief": True,
                "reason": "Out of water.",
            },
            "tu-assign-4",
        ),
        final_text="assigned",
    )

    result = await invoke(ctx, "r11", "help r11", model=model)

    assert result.stop_reason == "end_turn"
    assert len(tasks(ctx)) == 1
    # The captain was never asked. The only record is the volunteer's own task reply.
    made = ctx.store.decisions(ctx.incident_id)
    assert [(d.name, d.audience) for d in made] == [("doorstep-volunteer-update", "vol-priya")]


# --- escalate_to_captain: the tool body re-runs, and that is made harmless ------------------


async def test_escalation_pauses_and_re_running_the_tool_creates_one_decision(
    ctx: RunContext,
) -> None:
    with_result(
        ctx, "r01", CheckinStatus.URGENT, red_flags=["dizziness_fainting"], key_quote="I feel dizzy"
    )
    model = run_script(
        ToolCall(
            "escalate_to_captain",
            {
                "resident_id": "r01",
                "reason": "Said she is dizzy; told her to call 911.",
                "options": ["Call her GP"],
            },
            "tu-esc-1",
        ),
        final_text="captain paged",
    )

    paused = await invoke(ctx, "r01", "handle r01", model=model)
    assert paused.stop_reason == "interrupt"
    assert paused.interrupts[0].name == "doorstep-urgent-red-flag"
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.ESCALATED

    decision = ctx.store.decisions(ctx.incident_id)[0]
    labels = [o.label for o in decision.options]
    assert labels[0] == "I'm handling it"
    assert any(label.startswith("Send ") for label in labels)
    assert "Call the family contact" in labels  # r01 consented to family contact
    assert "Call her GP" in labels  # the model's own extra option survives

    await invoke(ctx, "r01", responses(paused, DECLINE), model=model)

    # The tool body ran twice — once to raise the interrupt, once on resume — and left one
    # decision and one escalation behind.
    assert len(ctx.store.decisions(ctx.incident_id)) == 1
    escalations = [
        t
        for t in ctx.store.case(ctx.incident_id, "r01").history
        if t.to_state == CaseState.ESCALATED
    ]
    assert len(escalations) == 1


async def test_i_am_handling_it_closes_the_case_even_if_the_model_forgets(ctx: RunContext) -> None:
    """A captain's choice must not depend on the model remembering to act on it.

    In a live drill the dispatcher closed one case after "I'm handling it" and silently left two
    others escalated with no outcome. The scripted model here never calls `close_case` at all —
    the worst case — and the case must still end up resolved and attributed to the captain.
    """
    from doorstep_agent.agents.dispatcher import _record_pauses
    from doorstep_agent.decisions import Responder, respond_to_decision

    with_result(
        ctx, "r01", CheckinStatus.URGENT, red_flags=["dizziness_fainting"], key_quote="dizzy"
    )
    model = run_script(
        ToolCall(
            "escalate_to_captain",
            {"resident_id": "r01", "reason": "Dizzy and confused.", "options": []},
            "tu-esc-2",
        ),
        final_text="the captain is handling it",  # note: no close_case
    )
    ctx.model_override = model
    paused = await invoke(ctx, "r01", "handle r01", model=model)
    decision = _record_pauses(ctx, "r01", paused)[0]

    outcome = await respond_to_decision(
        ctx,
        decision.id,
        "handle",
        Responder(source="drill", external_id="x"),
        actor_override="captain:cap-maria",
    )

    assert outcome.kind == "applied"
    case = ctx.store.case(ctx.incident_id, "r01")
    assert case.state == CaseState.RESOLVED
    assert case.outcome == "captain handling"
    assert case.history[-1].reason.startswith("captain:cap-maria")


async def test_the_captain_is_not_asked_twice_for_one_door_knock(ctx: RunContext) -> None:
    """SPEC §4.1 offers "Send Tom"; SPEC §7 interrupts every high-risk assignment.

    Taken literally the captain approves the same door-knock twice. Carrying out an answered
    decision is the authority for the assignment, so the second ask is skipped.
    """
    from doorstep_agent.models import Decision, DecisionOption

    with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])
    answered = Decision(
        id="dec-001",
        incident_id=ctx.incident_id,
        resident_id="r04",
        name="doorstep-urgent-red-flag",
        reason="already decided",
        options=[
            DecisionOption(
                id="send_volunteer",
                label="Send Tom (0.4 km)",
                action="assign_volunteer",
                args={"resident_id": "r04", "volunteer_id": "vol-tom"},
            )
        ],
        status="answered",
        response="send_volunteer",
        audience=ctx.org.captain_id,
    )
    ctx.store.save_decision(answered)
    model = run_script(
        ToolCall("assign_volunteer", _assign_input("r04"), "tu-assign-5"), final_text="assigned"
    )

    result = await invoke(
        ctx, "r04", "help r04", model=model, role="captain", answered_decision_id="dec-001"
    )

    assert result.stop_reason == "end_turn", "the captain must not be asked again"
    assert len(tasks(ctx)) == 1


def _assign_input(resident_id: str) -> dict[str, object]:
    return {
        "resident_id": resident_id,
        "volunteer_id": "vol-tom",
        "include_brief": True,
        "reason": "Needs a ride.",
    }


async def _escalate_and_choose_family(ctx: RunContext, model) -> None:
    from doorstep_agent.agents.dispatcher import _record_pauses
    from doorstep_agent.decisions import Responder, respond_to_decision

    with_result(ctx, "r05", CheckinStatus.NO_ANSWER)
    ctx.model_override = model
    paused = await invoke(ctx, "r05", "handle r05", model=model)
    decision = _record_pauses(ctx, "r05", paused)[0]
    assert "notify_family" in [o.id for o in decision.options]
    outcome = await respond_to_decision(
        ctx,
        decision.id,
        "notify_family",
        Responder(source="drill", external_id="x"),
        actor_override="captain:cap-maria",
    )
    assert outcome.kind == "applied"


def _escalate_r05() -> ToolCall:
    return ToolCall(
        "escalate_to_captain",
        {"resident_id": "r05", "reason": "No answer after three calls.", "options": []},
        "tu-esc-family",
    )


async def test_calling_the_family_happens_even_if_the_model_forgets(ctx: RunContext) -> None:
    """Found in the Gate 3 cloud drill: "Call the family contact" had no deterministic branch."""
    model = run_script(_escalate_r05(), final_text="the captain chose the family")  # no tool call
    await _escalate_and_choose_family(ctx, model)

    notices = [m for m in ctx.outbox if m.kind == "family_notice"]
    assert len(notices) == 1 and notices[0].resident_id == "r05"


async def test_the_family_hears_once_when_the_model_also_calls_the_tool(ctx: RunContext) -> None:
    from helpers.scripted_model import ScriptedModel, Turn

    model = ScriptedModel(
        [
            Turn(tool_calls=[_escalate_r05()]),
            Turn(
                tool_calls=[
                    ToolCall(
                        "notify_family",
                        {"resident_id": "r05", "reason": "Harold did not answer."},
                        "tu-family",
                    )
                ]
            ),
        ],
        final_text="done",
    )
    await _escalate_and_choose_family(ctx, model)

    assert model.model_calls >= 2, "the model must actually have called notify_family"
    assert len([m for m in ctx.outbox if m.kind == "family_notice"]) == 1


async def test_the_family_branch_still_needs_consent(ctx: RunContext) -> None:
    """The decision path skips Cedar, so the consent check is repeated in code."""
    from doorstep_agent.decisions import redrive_unapplied
    from doorstep_agent.models import Decision, DecisionOption, utcnow

    with_result(ctx, "r02", CheckinStatus.URGENT)  # r02 has no family consent
    ctx.store.save_decision(
        Decision(
            id="dec-900",
            incident_id=ctx.incident_id,
            resident_id="r02",
            name="doorstep-urgent-red-flag",
            reason="test",
            options=[DecisionOption(id="fam", label="Call family", action="notify_family")],
            status="answered",
            response="fam",
            responder="captain:cap-maria",
            responded_at=utcnow().replace(year=2000),
        )
    )
    redrive_unapplied(ctx)

    assert [m for m in ctx.outbox if m.kind == "family_notice"] == []
    assert any(
        e.policy_decision == "deny" and e.tool == "notify_family" for e in ctx.audit.events()
    )
