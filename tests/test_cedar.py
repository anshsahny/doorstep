"""Cedar policy tests (SPEC §8) through the real Strands `CedarAuthorization` handler.

Each test builds the handler exactly as the dispatcher does (policy files + generated schema +
our context enricher) and evaluates a hand-built `BeforeToolCallEvent`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from strands.agent.state import AgentState
from strands.hooks import BeforeToolCallEvent
from strands.interventions import Deny, Proceed

from doorstep_agent.models import CheckinAttempt
from doorstep_agent.policies import (
    SESSION_FIELDS,
    build_cedar,
    cedar_schema_for_tools,
    load_policy_text,
)
from doorstep_agent.runtime import RunContext
from doorstep_agent.tools import DISPATCH_TOOLS, READ_ONLY_TOOLS, start_simulated_checkin

ALL_TOOLS = [*READ_ONLY_TOOLS, *DISPATCH_TOOLS]


class _StubAgent:
    def __init__(self) -> None:
        self.state = AgentState()


def decide(ctx: RunContext, tool: str, tool_input: dict, **state):
    cedar = build_cedar(ctx)
    invocation_state = ctx.invocation_state(**state)
    event = BeforeToolCallEvent(
        agent=_StubAgent(),
        selected_tool=None,
        tool_use={"toolUseId": "t", "name": tool, "input": tool_input},
        invocation_state=invocation_state,
    )
    decision = cedar.before_tool_call(event)
    return decision, invocation_state["_cedar_session"]


def allowed(decision) -> bool:
    assert isinstance(decision, (Proceed, Deny)), decision
    return isinstance(decision, Proceed)


@pytest.fixture(autouse=True)
def _daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 14)
    monkeypatch.setenv("CALL_ALLOWLIST", "+15550000001,+15550000002")


# --- schema and startup validation ---


def test_policy_files_load_and_validate_against_the_generated_schema(ctx: RunContext) -> None:
    text = load_policy_text(ctx.settings.policies_dir)
    assert text.count("@id(") == 8
    schema = cedar_schema_for_tools(ALL_TOOLS)
    for field in SESSION_FIELDS:
        assert f"{field}:" in schema
    assert 'action "broadcast_to_volunteers"' in schema
    assert "include_resident_details: Bool" in schema
    assert "options: Set<String>" in schema
    build_cedar(ctx)  # no exception


def test_schema_covers_every_tool_of_every_agent(ctx: RunContext) -> None:
    """The policy files name every action, so the schema must know them all (drill regression)."""
    from doorstep_agent.tools import ALL_TOOLS as every_tool
    from doorstep_agent.tools import CHECKIN_TOOLS

    schema = cedar_schema_for_tools(every_tool)
    for tool in [*READ_ONLY_TOOLS, *DISPATCH_TOOLS, *CHECKIN_TOOLS]:
        assert f'action "{tool.tool_name}"' in schema, tool.tool_name
    assert len({t.tool_name for t in every_tool}) == len(every_tool)


def test_startup_validation_catches_action_and_context_typos(ctx: RunContext, tmp_path) -> None:
    (tmp_path / "bad.cedar").write_text(
        'permit(principal, action == Action::"get_rosterr", resource);'
    )
    with pytest.raises(ValueError, match="unrecognized action"):
        build_cedar(ctx, policies_dir=tmp_path)
    (tmp_path / "bad.cedar").write_text(
        'permit(principal, action == Action::"get_roster", resource) '
        'when { context.session.rolle == "captain" };'
    )
    with pytest.raises(ValueError, match="rolle"):
        build_cedar(ctx, policies_dir=tmp_path)
    (tmp_path / "bad.cedar").write_text(
        'permit(principal, action == Action::"get_roster", resource) '
        "when { context.session.mode == 3 };"
    )
    with pytest.raises(ValueError, match="not compatible"):
        build_cedar(ctx, policies_dir=tmp_path)


# --- rule 6: read-only always allowed ---


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("get_roster", {}),
        ("score_residents", {}),
        ("get_resident_memory", {"resident_id": "r01"}),
        ("find_relief_centres", {"kind": "cooling", "resident_id": ""}),
        ("find_nearest_volunteers", {"resident_id": "r01"}),
        ("schedule_recheck", {"resident_id": "r01", "minutes": 240, "reason": "evening"}),
        ("send_resident_tip", {"resident_id": "r01", "kind": "tip", "reason": "ok"}),
        ("escalate_to_captain", {"resident_id": "r01", "reason": "x", "options": []}),
        ("close_case", {"resident_id": "r01", "outcome": "ok", "reason": "fine"}),
    ],
)
def test_read_only_and_always_permitted_tools(ctx: RunContext, tool: str, tool_input: dict) -> None:
    decision, _ = decide(ctx, tool, tool_input)
    assert allowed(decision), decision


def test_unknown_tool_is_denied_fail_closed(ctx: RunContext) -> None:
    decision, _ = decide(ctx, "delete_everything", {})
    assert not allowed(decision)


# --- rule 1: real calls ---


def test_real_call_allowed_when_every_condition_holds(ctx_factory) -> None:
    ctx = ctx_factory(mode="live")  # r01 has phone_ref env:CALL_ALLOWLIST[0] and consents to calls
    decision, session = decide(
        ctx, "place_checkin_call", {"resident_id": "r01", "reason": "wave 1"}
    )
    assert allowed(decision), decision
    assert session["callee_allowlisted"] is True and session["callee_consented"] is True
    assert session["attempts_last_hour"] == 0 and session["local_hour"] == 14


def test_deny_call_to_non_allowlisted_number(ctx_factory) -> None:
    ctx = ctx_factory(mode="live")  # r02 has no phone_ref at all
    decision, session = decide(
        ctx, "place_checkin_call", {"resident_id": "r02", "reason": "wave 1"}
    )
    assert not allowed(decision)
    assert session["callee_allowlisted"] is False


def test_deny_call_in_drill_and_sandbox_modes(ctx_factory) -> None:
    for mode in ("drill", "sandbox"):
        decision, _ = decide(
            ctx_factory(mode=mode), "place_checkin_call", {"resident_id": "r01", "reason": "x"}
        )
        assert not allowed(decision), mode


def test_deny_call_without_consent(ctx_factory) -> None:
    ctx = ctx_factory(mode="live")
    ctx.store.resident("r01").consent.calls = False
    decision, session = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert not allowed(decision) and session["callee_consented"] is False


def test_deny_call_after_three_attempts_in_an_hour(ctx_factory) -> None:
    ctx = ctx_factory(mode="live")
    case = ctx.store.case(ctx.incident_id, "r01")
    for i in range(3):
        case.attempt_log.append(CheckinAttempt(attempt=i + 1, channel="phone"))
    decision, session = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert not allowed(decision) and session["attempts_last_hour"] == 3
    # Old attempts do not count.
    for a in case.attempt_log:
        a.started_at = a.started_at - timedelta(hours=2)
    decision, session = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert allowed(decision) and session["attempts_last_hour"] == 0


def test_quiet_hours_deny_unless_extreme(ctx_factory, monkeypatch) -> None:
    ctx = ctx_factory(mode="live")
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 23)
    decision, session = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert not allowed(decision) and session["local_hour"] == 23
    ctx.alert_severity = "Extreme"
    decision, _ = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert allowed(decision)
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 7)
    ctx.alert_severity = "Severe"
    decision, _ = decide(ctx, "place_checkin_call", {"resident_id": "r01", "reason": "x"})
    assert not allowed(decision)


# --- rule 2: sandbox never touches real channels ---


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("place_checkin_call", {"resident_id": "r01", "reason": "x"}),
        (
            "assign_volunteer",
            {"resident_id": "r01", "volunteer_id": "vol-tom", "include_brief": True, "reason": "x"},
        ),
        (
            "broadcast_to_volunteers",
            {"message": "water at the hall", "include_resident_details": False, "reason": "x"},
        ),
        ("notify_family", {"resident_id": "r01", "reason": "x"}),
    ],
)
def test_sandbox_forbid_beats_every_permit_on_a_real_channel(ctx_factory, tool, tool_input) -> None:
    ctx = ctx_factory(mode="sandbox")
    ctx.channel_override = "real"
    decision, session = decide(ctx, tool, tool_input)
    assert isinstance(decision, Deny)
    assert session["mode"] == "sandbox" and session["channel"] == "real"
    # The same call on the simulated channel is judged on its own merits.
    ctx.channel_override = None
    decision2, _ = decide(ctx, tool, tool_input)
    if tool != "place_checkin_call":
        assert allowed(decision2), tool


def test_simulated_checkins_only_outside_live(ctx_factory) -> None:
    payload = {"resident_id": "r01", "reason": "retry"}
    assert allowed(decide(ctx_factory(mode="drill"), "start_simulated_checkin", payload)[0])
    assert allowed(decide(ctx_factory(mode="sandbox"), "start_simulated_checkin", payload)[0])
    assert not allowed(decide(ctx_factory(mode="live"), "start_simulated_checkin", payload)[0])
    assert start_simulated_checkin.tool_name == "start_simulated_checkin"


# --- rule 3: resident details ---


def test_deny_group_broadcast_containing_resident_details(ctx: RunContext) -> None:
    decision, _ = decide(
        ctx,
        "broadcast_to_volunteers",
        {"message": "Rose in 3C is dizzy", "include_resident_details": True, "reason": "x"},
    )
    assert not allowed(decision)


def test_allow_group_broadcast_without_resident_details(ctx: RunContext) -> None:
    decision, _ = decide(
        ctx,
        "broadcast_to_volunteers",
        {
            "message": "Water is at the community hall",
            "include_resident_details": False,
            "reason": "x",
        },
    )
    assert allowed(decision)


def test_assign_volunteer_requires_available_and_within_3_km(ctx: RunContext) -> None:
    payload = {"resident_id": "r01", "include_brief": True, "reason": "no answer x3"}
    decision, session = decide(ctx, "assign_volunteer", {**payload, "volunteer_id": "vol-tom"})
    assert (
        allowed(decision)
        and session["volunteer_available"]
        and session["volunteer_distance_km"] == 0
    )
    decision, session = decide(ctx, "assign_volunteer", {**payload, "volunteer_id": "vol-helen"})
    assert not allowed(decision) and session["volunteer_available"] is False
    far = ctx.store.volunteer("vol-tom")
    far.lat += 0.05  # about 5.5 km north
    decision, session = decide(ctx, "assign_volunteer", {**payload, "volunteer_id": "vol-tom"})
    assert not allowed(decision) and session["volunteer_distance_km"] == 5
    decision, _ = decide(ctx, "assign_volunteer", {**payload, "volunteer_id": "vol-nobody"})
    assert not allowed(decision)


# --- rule 4: family consent ---


def test_family_contact_requires_consent(ctx: RunContext) -> None:
    decision, session = decide(ctx, "notify_family", {"resident_id": "r01", "reason": "x"})
    assert allowed(decision) and session["family_consent"] is True
    decision, session = decide(ctx, "notify_family", {"resident_id": "r02", "reason": "x"})
    assert not allowed(decision) and session["family_consent"] is False


# --- rule 5: emergency calls are recorded by humans only ---


def test_deny_agent_recording_an_emergency_call_but_allow_the_captain(ctx: RunContext) -> None:
    decision, session = decide(
        ctx, "record_emergency_call", {"resident_id": "r01", "by": "resident"}
    )
    assert not allowed(decision) and session["role"] == "agent"
    decision, session = decide(
        ctx, "record_emergency_call", {"resident_id": "r01", "by": "cap-maria"}, role="captain"
    )
    assert allowed(decision) and session["role"] == "captain"
    decision, _ = decide(
        ctx, "record_emergency_call", {"resident_id": "r01", "by": "vol-tom"}, role="volunteer"
    )
    assert not allowed(decision)
