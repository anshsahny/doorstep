"""The audit hook turns tool calls and Cedar denials into AuditEvents with reasons."""

from __future__ import annotations

from strands.agent.state import AgentState
from strands.hooks import AfterToolCallEvent

from doorstep_agent.audit import AuditHook, summarise_input
from doorstep_agent.runtime import RunContext


class _StubAgent:
    name = "dispatcher"

    def __init__(self) -> None:
        self.state = AgentState()


def _after_event(
    ctx: RunContext,
    tool: str,
    tool_input: dict,
    *,
    result: dict,
    cancel: str | None,
    session: dict | None = None,
):
    state = ctx.invocation_state(resident_id="r01", actor="agent:dispatcher")
    if session is not None:
        state["_cedar_session"] = session
    return AfterToolCallEvent(
        agent=_StubAgent(),
        selected_tool=None,
        tool_use={"toolUseId": "t1", "name": tool, "input": tool_input},
        invocation_state=state,
        result=result,
        cancel_message=cancel,
    )


def test_denial_becomes_a_policy_event_with_reason_and_session(ctx: RunContext) -> None:
    hook = AuditHook(actor="agent:dispatcher")
    session = {
        "mode": "drill",
        "role": "agent",
        "channel": "simulated",
        "callee_allowlisted": False,
    }
    event = _after_event(
        ctx,
        "record_emergency_call",
        {"resident_id": "r01", "by": "resident"},
        result={
            "toolUseId": "t1",
            "status": "error",
            "content": [{"text": "DENIED: Access denied by Cedar policy"}],
        },
        cancel="DENIED: Access denied by Cedar policy",
        session=session,
    )
    hook.after_tool_call(event)
    events = ctx.audit.events()
    assert len(events) == 1
    e = events[0]
    assert e.type == "policy" and e.policy_decision == "deny"
    assert e.tool == "record_emergency_call" and e.resident_id == "r01"
    assert e.reason.startswith("Access denied by Cedar policy")
    assert "decided on role=agent" in e.reason
    assert e.data["session"] == session
    assert e.actor == "agent:dispatcher"
    assert "DENY" in e.line() and "record_emergency_call" in e.line()
    assert ctx.audit.policy_denials() == [e]


def test_allowed_call_becomes_a_tool_call_event_with_rationale(ctx: RunContext) -> None:
    hook = AuditHook(actor="agent:dispatcher")
    event = _after_event(
        ctx,
        "schedule_recheck",
        {"resident_id": "r01", "minutes": 240, "reason": "evening re-check for an OK resident"},
        result={"toolUseId": "t1", "status": "success", "content": [{"text": "scheduled"}]},
        cancel=None,
        session={"mode": "drill"},
    )
    hook.after_tool_call(event)
    e = ctx.audit.events()[0]
    assert e.type == "tool_call" and e.policy_decision == "allow"
    assert e.rationale == "evening re-check for an OK resident"
    assert "minutes=240" in e.input_summary
    assert e.data["status"] == "success"
    assert ctx.audit.policy_denials() == []


def test_tool_error_is_recorded_as_reason(ctx: RunContext) -> None:
    hook = AuditHook(actor="agent:dispatcher")
    event = _after_event(
        ctx,
        "close_case",
        {"resident_id": "r01", "outcome": "ok", "reason": "fine"},
        result={"toolUseId": "t1", "status": "error", "content": [{"text": "error: cannot close"}]},
        cancel=None,
    )
    hook.after_tool_call(event)
    e = ctx.audit.events()[0]
    assert e.type == "tool_call" and e.reason.startswith("error: cannot close")
    assert e.policy_decision is None  # no Cedar session recorded for this call


def test_hook_ignores_events_without_a_run_context() -> None:
    hook = AuditHook(actor="x")
    event = AfterToolCallEvent(
        agent=_StubAgent(),
        selected_tool=None,
        tool_use={"toolUseId": "t", "name": "x", "input": {}},
        invocation_state={},
        result={"toolUseId": "t", "status": "success", "content": []},
    )
    hook.after_tool_call(event)  # no exception


def test_summarise_input_hides_long_free_text() -> None:
    s = summarise_input(
        {"resident_id": "r01", "message": "x" * 500, "include_resident_details": False}
    )
    assert "message=<500 chars>" in s and "resident_id=r01" in s and "false" in s
    assert len(summarise_input({"k": "v" * 400})) <= 160
