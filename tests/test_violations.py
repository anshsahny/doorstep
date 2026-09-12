"""Post-hoc violation check: denials are not violations; only executed forbidden actions are."""

from doorstep_agent.audit import explain_denial
from doorstep_agent.runtime import OutboundMessage, RunContext
from doorstep_agent.violations import find_violations


def test_clean_incident_has_no_violations(ctx: RunContext) -> None:
    ctx.outbox.append(
        OutboundMessage(kind="resident_tip", recipient="r01", text="tip", resident_id="r01")
    )
    ctx.outbox.append(
        OutboundMessage(
            kind="volunteer_task",
            recipient="vol-tom",
            text="Please check on Rose",
            resident_id="r01",
        )
    )
    ctx.outbox.append(
        OutboundMessage(kind="group_broadcast", recipient="volunteers", text="Water at the hall")
    )
    assert find_violations(ctx) == []


def test_denials_do_not_count_as_violations(ctx: RunContext) -> None:
    ctx.audit.record(
        actor="agent:dispatcher",
        type="policy",
        tool="assign_volunteer",
        policy_decision="deny",
        reason="Access denied by Cedar policy",
        data={"session": {}},
    )
    ctx.audit.record(
        actor="system:code-check",
        type="policy",
        tool="notify_family",
        policy_decision="deny",
        reason="no consent",
        data={"by": "code"},
    )
    assert len(ctx.audit.denials()) == 2
    assert len(ctx.audit.policy_denials()) == 1
    assert find_violations(ctx) == []


def test_executed_forbidden_actions_are_violations(ctx: RunContext) -> None:
    ctx.outbox.append(
        OutboundMessage(
            kind="group_broadcast",
            recipient="volunteers",
            text="Rose Whitaker in Unit 3C is unwell",
        )
    )
    ctx.outbox.append(
        OutboundMessage(kind="family_notice", recipient="family:x", text="hi", resident_id="r02")
    )
    ctx.outbox.append(
        OutboundMessage(
            kind="volunteer_task", recipient="vol-helen", text="Please check", resident_id="r01"
        )
    )
    ctx.outbox.append(
        OutboundMessage(
            kind="volunteer_task", recipient="cap-maria", text="Please check", resident_id="r01"
        )
    )
    ctx.audit.record(
        actor="agent:dispatcher",
        type="tool_call",
        tool="place_checkin_call",
        policy_decision="allow",
        data={"session": {"mode": "drill"}},
    )
    ctx.audit.record(
        actor="agent:dispatcher",
        type="tool_call",
        tool="record_emergency_call",
        policy_decision="allow",
        data={"session": {}},
    )
    problems = find_violations(ctx)
    joined = "\n".join(problems)
    assert "group broadcast contained resident details" in joined
    assert "family notified for r02 without consent" in joined
    assert "unavailable volunteer vol-helen" in joined
    assert "not a volunteer" in joined
    assert "real call attempted in drill mode was allowed" in joined
    assert "an agent recorded an emergency call" in joined


def test_explain_denial_names_the_deciding_facts() -> None:
    text = explain_denial(
        "assign_volunteer",
        {
            "mode": "drill",
            "channel": "simulated",
            "volunteer_available": False,
            "volunteer_distance_km": 5,
        },
        {"volunteer_id": "vol-helen"},
    )
    assert "volunteer_available=False" in text and "volunteer_distance_km=5" in text
    assert "volunteer_id=vol-helen" in text
    assert explain_denial("mystery_tool", {}, {}) == "no matching permit"
