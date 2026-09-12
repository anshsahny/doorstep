"""ModelCallGuard: bounds one agent invocation to N model calls and audits the cut."""

from strands.agent.state import AgentState
from strands.hooks import BeforeInvocationEvent, BeforeModelCallEvent

from doorstep_agent.guards import ModelCallGuard
from doorstep_agent.runtime import RunContext


class _StubAgent:
    name = "checkin_text"

    def __init__(self) -> None:
        self.state = AgentState()


def test_guard_cancels_after_the_cap_and_resets_per_invocation(ctx: RunContext) -> None:
    guard = ModelCallGuard(max_calls=3)
    agent = _StubAgent()
    state = ctx.invocation_state(resident_id="r09")

    guard.reset(BeforeInvocationEvent(agent=agent, invocation_state=state))
    for _ in range(3):
        event = BeforeModelCallEvent(agent=agent, invocation_state=state)
        guard.before_model_call(event)
        assert event.cancel is False and guard.tripped is False

    fourth = BeforeModelCallEvent(agent=agent, invocation_state=state)
    guard.before_model_call(fourth)
    assert guard.tripped is True
    assert "stopped after 3 model calls" in str(fourth.cancel)
    note = ctx.audit.events()[-1]
    assert note.type == "note" and note.resident_id == "r09"
    assert "model-call guard" in note.reason

    guard.reset(BeforeInvocationEvent(agent=agent, invocation_state=state))
    assert guard.calls == 0 and guard.tripped is False
