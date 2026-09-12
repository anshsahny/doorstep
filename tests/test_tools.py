"""Code-level checks inside the tools (the second line of defence after Cedar), no network."""

from __future__ import annotations

from strands.agent.state import AgentState
from strands.types.tools import ToolContext

from doorstep_agent.models import CaseState, CheckinResult, CheckinStatus
from doorstep_agent.runtime import RunContext
from doorstep_agent.state_machine import transition
from doorstep_agent.tools import (
    assign_volunteer,
    broadcast_to_volunteers,
    close_case,
    escalate_to_captain,
    find_nearest_volunteers,
    find_relief_centres,
    notify_family,
    place_checkin_call,
    record_emergency_call,
    send_resident_tip,
)


class _StubAgent:
    name = "test"

    def __init__(self) -> None:
        self.state = AgentState()


def tc(ctx: RunContext, **state) -> ToolContext:
    return ToolContext(
        tool_use={"toolUseId": "t", "name": "x", "input": {}},
        agent=_StubAgent(),  # type: ignore[arg-type]
        invocation_state=ctx.invocation_state(actor="agent:test", **state),
    )


def _with_result(ctx: RunContext, rid: str, status: CheckinStatus, **kw) -> None:
    case = ctx.store.case(ctx.incident_id, rid)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=status, **kw))


def test_relief_centres_use_the_profile_kind_and_distance(ctx: RunContext) -> None:
    rows = find_relief_centres(
        kind=ctx.profile.relief_centre_kind, resident_id="r01", tool_context=tc(ctx)
    )
    assert [r["centre_id"] for r in rows][:2] == ["rc-mt-scott", "rc-holgate"]
    assert rows[0]["distance_km"] < rows[-1]["distance_km"]
    assert find_relief_centres(kind="shelter", resident_id="", tool_context=tc(ctx)) == []


def test_nearest_volunteers_excludes_the_captain_and_sorts_available_first(ctx: RunContext) -> None:
    rows = find_nearest_volunteers(resident_id="r01", tool_context=tc(ctx))
    assert all(r["role"] == "volunteer" for r in rows)
    assert rows[0]["available"] is True
    assert rows[-1]["volunteer_id"] == "vol-helen"  # unavailable sorts last


def test_broadcast_refuses_resident_details_even_if_the_flag_says_none(ctx: RunContext) -> None:
    out = broadcast_to_volunteers(
        message="Rose Whitaker in Unit 3C needs water",
        include_resident_details=False,
        reason="x",
        tool_context=tc(ctx),
    )
    assert out.startswith("refused:")
    assert ctx.outbox == []
    denial = ctx.audit.denials()[-1]
    assert denial.tool == "broadcast_to_volunteers" and denial.data["by"] == "code"
    ok = broadcast_to_volunteers(
        message="Water and ice are at the community hall until 8 PM",
        include_resident_details=False,
        reason="x",
        tool_context=tc(ctx),
    )
    assert (
        ok == "broadcast sent to the volunteer group" and ctx.outbox[-1].kind == "group_broadcast"
    )


def test_notify_family_needs_consent_and_a_contact(ctx: RunContext) -> None:
    assert notify_family(resident_id="r02", reason="x", tool_context=tc(ctx)).startswith("refused:")
    out = notify_family(
        resident_id="r01", reason="She may need a hand today.", tool_context=tc(ctx)
    )
    assert "notified" in out
    msg = ctx.outbox[-1]
    assert msg.kind == "family_notice" and msg.recipient == "family:contact:r01-daughter"
    assert "Rose" in msg.text and "dizzy" not in msg.text


def test_real_calls_are_refused_outside_live_mode(ctx: RunContext) -> None:
    out = place_checkin_call(resident_id="r01", reason="x", tool_context=tc(ctx))
    assert out.startswith("refused:") and "drill" in out
    assert ctx.audit.denials()[-1].tool == "place_checkin_call"


def test_record_emergency_call_is_humans_only_in_code_too(ctx: RunContext) -> None:
    assert record_emergency_call(resident_id="r01", by="resident", tool_context=tc(ctx)).startswith(
        "refused:"
    )
    assert (
        record_emergency_call(
            resident_id="r01", by="cap-maria", tool_context=tc(ctx, role="captain")
        )
        == "recorded"
    )


def test_escalate_needs_a_result_first(ctx: RunContext) -> None:
    """The guard clauses run before the interrupt, so a direct call still reaches them."""
    out = escalate_to_captain(resident_id="r06", reason="x", options=[], tool_context=tc(ctx))
    assert out.startswith("error:")


def test_assign_volunteer_body_never_runs_unapproved_for_high_risk(ctx: RunContext) -> None:
    """The tool itself no longer asks: approval happens at admission, in `ApprovalHook`.

    Called directly — the way only a test can — the body just runs, which is exactly why the
    guard cannot live here. What stops an unapproved high-risk task leaving the building is the
    hook pausing before this function is ever entered (test_interrupt_resume.py).
    """
    _with_result(ctx, "r04", CheckinStatus.NEEDS_HELP, needs=["ride"])  # r04 scores 10 -> wave 1
    assert ctx.store.case(ctx.incident_id, "r04").risk.wave == 1
    out = assign_volunteer(
        resident_id="r04",
        volunteer_id="vol-tom",
        include_brief=True,
        reason="Needs a ride.",
        tool_context=tc(ctx),
    )
    assert out.startswith("task sent to Tom")
    assert ctx.store.case(ctx.incident_id, "r04").state == CaseState.ASSIGNED


def test_assign_volunteer_low_risk_goes_straight_through_with_minimal_brief(
    ctx: RunContext,
) -> None:
    _with_result(ctx, "r11", CheckinStatus.NEEDS_HELP, needs=["water"])  # r11 scores 3 -> wave 3
    out = assign_volunteer(
        resident_id="r11",
        volunteer_id="vol-priya",
        include_brief=True,
        reason="Out of water.",
        tool_context=tc(ctx),
    )
    assert out.startswith("task sent to Priya")
    task = ctx.outbox[-1]
    assert "Anita" in task.text and "Out of water" in task.text
    # No captain decision; the one record is Priya's own reply, with her three buttons.
    made = ctx.store.decisions(ctx.incident_id)
    assert len(made) == 1
    assert made[0].name == "doorstep-volunteer-update" and made[0].audience == "vol-priya"
    assert [o.label for o in made[0].options] == ["On my way", "They're OK", "Need more help"]


def test_assign_volunteer_refuses_unavailable_or_far_in_code(ctx: RunContext) -> None:
    _with_result(ctx, "r11", CheckinStatus.NEEDS_HELP, needs=["water"])
    out = assign_volunteer(
        resident_id="r11",
        volunteer_id="vol-helen",
        include_brief=True,
        reason="x",
        tool_context=tc(ctx),
    )
    assert out.startswith("refused:")
    out = assign_volunteer(
        resident_id="r11",
        volunteer_id="cap-maria",
        include_brief=True,
        reason="x",
        tool_context=tc(ctx),
    )
    assert out.startswith("refused:")


def test_send_resident_tip_uses_profile_text_in_the_residents_language(ctx: RunContext) -> None:
    out = send_resident_tip(resident_id="r03", kind="tip", reason="x", tool_context=tc(ctx))
    assert ctx.profile.tip("es") in out
    out = send_resident_tip(
        resident_id="r01", kind="relief_centres", reason="x", tool_context=tc(ctx)
    )
    assert "Mt. Scott Community Center" in out and "Holgate Library" in out
    assert send_resident_tip(
        resident_id="r01", kind="nope", reason="x", tool_context=tc(ctx)
    ).startswith("error:")


def test_close_case_rules(ctx: RunContext) -> None:
    assert close_case(resident_id="r06", outcome="ok", reason="x", tool_context=tc(ctx)).startswith(
        "error:"
    )
    _with_result(ctx, "r06", CheckinStatus.OK)
    assert (
        close_case(resident_id="r06", outcome="ok", reason="fine", tool_context=tc(ctx))
        == "case r06 closed: ok"
    )
    assert ctx.store.case(ctx.incident_id, "r06").outcome == "ok"
    _with_result(ctx, "r01", CheckinStatus.URGENT)
    assert close_case(resident_id="r01", outcome="ok", reason="x", tool_context=tc(ctx)).startswith(
        "error:"
    )
    case = ctx.store.case(ctx.incident_id, "r01")
    transition(case, CaseState.ESCALATED, "test")
    assert close_case(
        resident_id="r01", outcome="captain_handling", reason="x", tool_context=tc(ctx)
    ).endswith("captain_handling")
