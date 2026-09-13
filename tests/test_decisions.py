"""`respond_to_decision`: who may answer, and what a second answer does (Gate 2).

Every channel — Telegram now, the dashboard in Phase 5 — goes through this one function, so
these are the rules for all of them at once. Two things are being pinned down:

* a decision leaves `pending` exactly once, whatever happens afterwards;
* the person answering is checked against the roster, not trusted from the channel.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from conftest import make_ctx
from doorstep_agent.decisions import (
    Responder,
    expire_due_decisions,
    respond_to_decision,
    upsert_decision,
)
from doorstep_agent.models import CaseState, Decision, DecisionOption
from doorstep_agent.runtime import RunContext

CAPTAIN_CHAT = "42424242"
TOM_CHAT = "51515151"
PRIYA_CHAT = "52525252"
STRANGER_CHAT = "99999999"


@pytest.fixture(autouse=True)
def chat_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct chat ids per person, so the role check is proven and not a coincidence."""
    monkeypatch.setenv("TELEGRAM_CAPTAIN_CHAT_ID", CAPTAIN_CHAT)
    monkeypatch.setenv("TELEGRAM_VOLUNTEER_CHAT_IDS", f"{TOM_CHAT},{PRIYA_CHAT}")


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return make_ctx(auto_approve=False, sessions_dir=tmp_path / "sessions")


def a_decision(
    ctx: RunContext,
    *,
    name: str = "doorstep-urgent-red-flag",
    audience: str = "cap-maria",
    resident_id: str = "r01",
    status: str = "pending",
) -> Decision:
    """A decision with no interrupt attached, so answering it applies directly."""
    decision = upsert_decision(
        ctx,
        tool_use_id=f"tu-{name}-{resident_id}",
        resident_id=resident_id,
        name=name,
        reason="Needs a decision.",
        options=[
            DecisionOption(
                id="handle",
                label="I'm handling it",
                action="resolve",
                args={"resident_id": resident_id, "outcome": "captain handling"},
            ),
            DecisionOption(id="note", label="Log it", action="acknowledge"),
        ],
        audience=audience,
    )
    decision.status = status  # type: ignore[assignment]
    decision.expires_at = ctx.clock.after(ctx.settings.decision_ttl_minutes)
    ctx.store.save_decision(decision)
    return decision


def escalate(ctx: RunContext, resident_id: str) -> None:
    """Put a case where an escalation leaves it: urgent, waiting on the captain."""
    from doorstep_agent.models import CheckinResult, CheckinStatus
    from doorstep_agent.state_machine import transition

    case = ctx.store.case(ctx.incident_id, resident_id)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.URGENT))
    transition(case, CaseState.ESCALATED, reason="escalated to captain")
    ctx.store.save_case(case)


# --- identity ------------------------------------------------------------------------------


async def test_a_stranger_cannot_answer_anything(ctx: RunContext) -> None:
    decision = a_decision(ctx)

    outcome = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=STRANGER_CHAT)
    )

    assert outcome.kind == "forbidden"
    assert "not on the Juniper Court list" in outcome.detail
    assert ctx.store.decision(decision.id).status == "pending", "still open for the real captain"
    denials = [e for e in ctx.audit.events() if e.policy_decision == "deny"]
    assert denials and "answer refused" in denials[-1].reason


async def test_a_volunteer_cannot_answer_a_captains_decision(ctx: RunContext) -> None:
    decision = a_decision(ctx)

    outcome = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=TOM_CHAT)
    )

    assert outcome.kind == "forbidden"
    assert "addressed to cap-maria" in outcome.detail
    assert ctx.store.decision(decision.id).status == "pending"


async def test_a_volunteer_cannot_answer_another_volunteers_task(ctx: RunContext) -> None:
    decision = a_decision(ctx, name="doorstep-volunteer-update", audience="vol-tom")

    outcome = await respond_to_decision(
        ctx, decision.id, "note", Responder(source="telegram", external_id=PRIYA_CHAT)
    )

    assert outcome.kind == "forbidden"
    assert "addressed to vol-tom" in outcome.detail


async def test_the_captain_can_answer_their_own_decision(ctx: RunContext) -> None:
    escalate(ctx, "r01")
    decision = a_decision(ctx)

    outcome = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )

    assert outcome.kind == "applied"
    saved = ctx.store.decision(decision.id)
    assert saved.status == "answered" and saved.responder == "captain:cap-maria"
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.RESOLVED


async def test_the_assigned_volunteer_can_answer_their_own_task(ctx: RunContext) -> None:
    decision = a_decision(ctx, name="doorstep-volunteer-update", audience="vol-tom")

    outcome = await respond_to_decision(
        ctx, decision.id, "note", Responder(source="telegram", external_id=TOM_CHAT)
    )

    assert outcome.kind == "applied"
    assert ctx.store.decision(decision.id).responder == "volunteer:vol-tom"


async def test_the_web_path_uses_the_same_rules(ctx: RunContext) -> None:
    """Phase 5's dashboard changes the source, not the rules.

    The API verifies a signed token and names the subject; a Telegram chat id sent from a browser
    is not an identity, so knowing the captain's chat id does not let anyone answer.
    """
    escalate(ctx, "r01")
    decision = a_decision(ctx)

    for subject in (STRANGER_CHAT, CAPTAIN_CHAT, f"sandbox:{ctx.incident_id}", "sandbox:other"):
        refused = await respond_to_decision(
            ctx, decision.id, "handle", Responder(source="web", external_id=subject)
        )
        assert refused.kind == "forbidden", subject

    allowed = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="web", external_id="captain")
    )
    assert allowed.kind == "applied"
    assert ctx.store.decision(decision.id).responder == "captain:cap-maria"


async def test_a_sandbox_visitor_answers_only_their_own_sandbox_drill(ctx: RunContext) -> None:
    ctx.mode = "sandbox"
    escalate(ctx, "r01")
    decision = a_decision(ctx)

    other = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="web", external_id="sandbox:drill-someone")
    )
    assert other.kind == "forbidden"
    mine = await respond_to_decision(
        ctx,
        decision.id,
        "handle",
        Responder(source="web", external_id=f"sandbox:{ctx.incident_id}"),
    )
    assert mine.kind == "applied"
    again = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )
    assert again.kind == "already_answered", "a Telegram tap after the dashboard is a no-op"


async def test_one_phone_playing_two_roles_resolves_to_the_addressee(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The solo-demo setup: one Telegram account is both the captain and vol-tom.

    A shared chat id is ambiguous, so resolution picks the member the decision was addressed to.
    Without this, whichever record sorted first would answer everything and the role check would
    look like it passed by luck. This is the configuration the manual Gate 2 run uses, so it is
    worth pinning; the tests above use distinct ids to prove the role check for real.
    """
    monkeypatch.setenv("TELEGRAM_VOLUNTEER_CHAT_IDS", CAPTAIN_CHAT)  # one phone, both parts
    one_phone = Responder(source="telegram", external_id=CAPTAIN_CHAT)
    escalate(ctx, "r01")

    captain_call = a_decision(ctx)
    volunteer_task = a_decision(
        ctx, name="doorstep-volunteer-update", audience="vol-tom", resident_id="r04"
    )

    assert (await respond_to_decision(ctx, captain_call.id, "handle", one_phone)).kind == "applied"
    assert (await respond_to_decision(ctx, volunteer_task.id, "note", one_phone)).kind == "applied"

    assert ctx.store.decision(captain_call.id).responder == "captain:cap-maria"
    assert ctx.store.decision(volunteer_task.id).responder == "volunteer:vol-tom"


async def test_send_a_volunteer_actually_sends_them(ctx: RunContext) -> None:
    """ "Send Sam" must send Sam, whether or not the model calls the tool afterwards.

    In the manual Gate 2 run the captain chose "Send Sam" for both urgent residents, the model
    never called `assign_volunteer`, and nobody was sent. There is no agent paused on this
    decision at all, so nothing but this branch can carry it out.
    """
    escalate(ctx, "r01")
    decision = upsert_decision(
        ctx,
        tool_use_id="tu-send",
        resident_id="r01",
        name="doorstep-urgent-red-flag",
        reason="Dizzy and confused; someone should go now.",
        options=[
            DecisionOption(
                id="send_volunteer",
                label="Send Sam (0.2 km)",
                action="assign_volunteer",
                args={"resident_id": "r01", "volunteer_id": "vol-sam"},
            )
        ],
        audience="cap-maria",
    )
    decision.status = "pending"
    ctx.store.save_decision(decision)

    outcome = await respond_to_decision(
        ctx, decision.id, "send_volunteer", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )

    assert outcome.kind == "applied"
    case = ctx.store.case(ctx.incident_id, "r01")
    assert case.state == CaseState.ASSIGNED
    assert case.assigned_volunteer == "vol-sam"
    tasks = [m for m in ctx.outbox if m.kind == "volunteer_task"]
    assert len(tasks) == 1 and tasks[0].recipient == "vol-sam"
    # Sam gets his own three buttons, addressed to him and nobody else.
    reply = [d for d in ctx.store.decisions(ctx.incident_id) if d.audience == "vol-sam"]
    assert len(reply) == 1 and reply[0].name == "doorstep-volunteer-update"


async def test_sending_a_volunteer_twice_sends_one_task(ctx: RunContext) -> None:
    """The branch is idempotent, so it is safe to run after a model that already assigned."""
    from doorstep_agent.tools import send_volunteer_task

    escalate(ctx, "r01")
    resident, volunteer = ctx.store.resident("r01"), ctx.store.volunteer("vol-sam")
    first = send_volunteer_task(
        ctx, resident, volunteer, "Go now.", include_brief=True, tool_use_id="tu-1"
    )
    second = send_volunteer_task(
        ctx, resident, volunteer, "Go now.", include_brief=True, tool_use_id="tu-2"
    )

    assert first.startswith("task sent to Sam")
    assert "already assigned" in second
    assert len([m for m in ctx.outbox if m.kind == "volunteer_task"]) == 1


def test_a_decision_deadline_is_real_time_not_drill_time(ctx: RunContext) -> None:
    """Compression speeds up the agent's timers, never the human's thinking.

    The manual Gate 2 run expired a decision 7 seconds before the captain's thumb landed, because
    a 15-minute deadline had been divided by the drill's compression factor into 30 seconds.
    """
    from doorstep_agent.decisions import expires_at

    assert ctx.clock.compression == 30, "this drill context is compressed"
    ttl = expires_at(ctx) - ctx.clock.now()

    assert ttl.total_seconds() == pytest.approx(15 * 60, abs=2)
    # The agent's own retry timer is still compressed, which is the whole point of the factor.
    assert ctx.clock.after(10) - ctx.clock.now() < timedelta(seconds=25)


# --- idempotency ---------------------------------------------------------------------------


async def test_answering_twice_is_recognised_not_replayed(ctx: RunContext) -> None:
    escalate(ctx, "r01")
    decision = a_decision(ctx)
    captain = Responder(source="telegram", external_id=CAPTAIN_CHAT)

    first = await respond_to_decision(ctx, decision.id, "handle", captain)
    second = await respond_to_decision(ctx, decision.id, "handle", captain)

    assert first.kind == "applied"
    assert second.kind == "already_answered"
    assert "Nothing was sent twice" in second.message
    assert ctx.store.decision(decision.id).response == "handle"
    resolutions = [
        t
        for t in ctx.store.case(ctx.incident_id, "r01").history
        if t.to_state == CaseState.RESOLVED
    ]
    assert len(resolutions) == 1


async def test_a_second_tap_choosing_differently_still_loses(ctx: RunContext) -> None:
    """The first answer stands; a later tap cannot overwrite a decision already acted on."""
    escalate(ctx, "r01")
    decision = a_decision(ctx)
    captain = Responder(source="telegram", external_id=CAPTAIN_CHAT)

    await respond_to_decision(ctx, decision.id, "handle", captain)
    second = await respond_to_decision(ctx, decision.id, "note", captain)

    assert second.kind == "already_answered"
    assert "Already answered" in second.message and "I'm handling it" in second.message
    assert ctx.store.decision(decision.id).response == "handle"


async def test_answering_after_a_timeout_says_so_and_does_nothing(ctx: RunContext) -> None:
    escalate(ctx, "r01")
    decision = a_decision(ctx)
    decision.expires_at = ctx.clock.now() - timedelta(seconds=1)
    ctx.store.save_decision(decision)

    assert [d.id for d in expire_due_decisions(ctx)] == [decision.id]

    outcome = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )

    assert outcome.kind == "expired"
    assert "nothing was sent" in outcome.message
    # Expiry must never decide for the human: the case stays where the agent left it.
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.ESCALATED


async def test_expiry_leaves_answered_and_unexpired_decisions_alone(ctx: RunContext) -> None:
    escalate(ctx, "r01")
    answered = a_decision(ctx)
    await respond_to_decision(
        ctx, answered.id, "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )
    still_open = a_decision(ctx, resident_id="r04")

    assert expire_due_decisions(ctx) == []
    assert ctx.store.decision(answered.id).status == "answered"
    assert ctx.store.decision(still_open.id).status == "pending"


async def test_an_unknown_option_is_refused(ctx: RunContext) -> None:
    decision = a_decision(ctx)

    outcome = await respond_to_decision(
        ctx, decision.id, "evacuate", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )

    assert outcome.kind == "unknown_option"
    assert ctx.store.decision(decision.id).status == "pending"


async def test_a_draft_decision_cannot_be_answered_yet(ctx: RunContext) -> None:
    """Nothing is deliverable before the runner stamps the interrupt on it."""
    decision = a_decision(ctx, status="draft")

    outcome = await respond_to_decision(
        ctx, decision.id, "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )

    assert outcome.kind == "not_found"
    assert ctx.store.decision(decision.id).status == "draft"


async def test_an_unknown_decision_is_refused(ctx: RunContext) -> None:
    outcome = await respond_to_decision(
        ctx, "dec-999", "handle", Responder(source="telegram", external_id=CAPTAIN_CHAT)
    )
    assert outcome.kind == "not_found"


# --- the record itself -----------------------------------------------------------------------


def test_one_tool_use_makes_one_decision_however_often_the_tool_runs(ctx: RunContext) -> None:
    """The tool body re-runs on resume; the record it created must not be duplicated."""
    made = [
        upsert_decision(
            ctx,
            tool_use_id="tu-same",
            resident_id="r01",
            name="doorstep-urgent-red-flag",
            reason="Dizzy.",
            options=[DecisionOption(id="handle", label="I'm handling it", action="resolve")],
            audience="cap-maria",
        )
        for _ in range(3)
    ]

    assert {d.id for d in made} == {"dec-001"}
    assert len(ctx.store.decisions(ctx.incident_id)) == 1
