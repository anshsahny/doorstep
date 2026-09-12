"""Build a real Doorstep dispatcher driven by a scripted model.

Everything here is the production agent — the same tools, Cedar intervention, audit hook,
approval hook and session manager — with only the model provider swapped, so interrupt and
resume behaviour under test is the SDK's and the wiring under test is Doorstep's.
"""

from __future__ import annotations

from typing import Any

from strands.agent import AgentResult

from doorstep_agent.agents.dispatcher import AGENT_NAME, build_dispatcher
from doorstep_agent.runtime import RunContext

from .scripted_model import ScriptedModel, ToolCall, Turn

__all__ = ["AGENT_NAME", "ScriptedModel", "ToolCall", "Turn", "invoke", "responses", "run_script"]


async def invoke(
    ctx: RunContext,
    resident_id: str,
    prompt: Any,
    *,
    model: ScriptedModel,
    role: str = "agent",
    **state: Any,
) -> AgentResult:
    agent = build_dispatcher(ctx, resident_id, model=model)
    invocation_state = ctx.invocation_state(
        resident_id=resident_id, actor=f"agent:{AGENT_NAME}", **state
    )
    invocation_state["role"] = role
    return await agent.invoke_async(prompt, invocation_state=invocation_state)


def responses(result: AgentResult, payload: Any) -> list[dict[str, Any]]:
    """The resume prompt: one `interruptResponse` per interrupt the agent raised."""
    return [
        {"interruptResponse": {"interruptId": i.id, "response": payload}}
        for i in (result.interrupts or [])
    ]


def run_script(*calls: ToolCall, final_text: str = "done") -> ScriptedModel:
    """A model that makes these tool calls in one turn, then answers."""
    return ScriptedModel([Turn(tool_calls=list(calls))], final_text=final_text)
