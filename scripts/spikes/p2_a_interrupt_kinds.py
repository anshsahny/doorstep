"""Spike A: tool interrupt vs BeforeToolCallEvent interrupt — what runs, and how many times.

The Phase 2 design rests on one fact: a tool interrupt re-runs the tool body from the top on
resume, while a BeforeToolCallEvent interrupt stops the tool from running at all until approved.
This spike proves both in the installed SDK rather than inferring them from the source.

Expected:
  tool interrupt        pre-interrupt work runs TWICE, post-interrupt work runs ONCE
  hook interrupt        tool body runs ZERO times at the pause, ONCE after approval,
                        ZERO times when the response declines (cancel_tool)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
from strands.types.tools import ToolContext

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from helpers.scripted_model import ScriptedModel, ToolCall, Turn  # noqa: E402

LOG: list[str] = []


@tool(context=True)
def escalate(resident_id: str, tool_context: ToolContext) -> str:
    """Page the captain and wait for their choice.

    Args:
        resident_id: The resident's id.
    """
    LOG.append(f"escalate:pre:{resident_id}")
    choice = tool_context.interrupt("spike-escalate", reason={"resident_id": resident_id})
    LOG.append(f"escalate:post:{choice}")
    return f"captain chose {choice}"


@tool
def assign(resident_id: str, volunteer_id: str) -> str:
    """Send a volunteer a task.

    Args:
        resident_id: The resident's id.
        volunteer_id: The volunteer's id.
    """
    LOG.append(f"assign:body:{resident_id}:{volunteer_id}")
    return f"task sent to {volunteer_id}"


class ApprovalHook(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "assign":
            return
        LOG.append("assign:hook")
        answer = event.interrupt("spike-assign", reason=dict(event.tool_use["input"]))
        if answer != "approve":
            event.cancel_tool = f"captain chose: {answer}"


def build(turns: list[Turn]) -> Agent:
    return Agent(
        model=ScriptedModel(turns),
        tools=[escalate, assign],
        hooks=[ApprovalHook()],
        callback_handler=None,
    )


def respond(result: Any, answer: Any) -> list[dict[str, Any]]:
    return [
        {"interruptResponse": {"interruptId": i.id, "response": answer}} for i in result.interrupts
    ]


def check(label: str, got: Any, want: Any) -> bool:
    good = got == want
    print(f"  {'PASS' if good else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    return good


def main() -> int:
    ok = True

    print("\n1. tool interrupt (tool_context.interrupt) on `escalate`")
    LOG.clear()
    agent = build([Turn(tool_calls=[ToolCall("escalate", {"resident_id": "r01"}, "tu-1")])])
    result = agent("handle r01")
    ok &= check("stop_reason at pause", result.stop_reason, "interrupt")
    ok &= check("interrupt name", result.interrupts[0].name, "spike-escalate")
    ok &= check("interrupt reason", result.interrupts[0].reason, {"resident_id": "r01"})
    ok &= check("log at pause", LOG, ["escalate:pre:r01"])
    paused_id = result.interrupts[0].id

    result = agent(respond(result, "send_volunteer"))
    ok &= check("stop_reason after resume", result.stop_reason, "end_turn")
    ok &= check(
        "log after resume (pre runs TWICE)",
        LOG,
        ["escalate:pre:r01", "escalate:pre:r01", "escalate:post:send_volunteer"],
    )
    print(f"       interrupt id: {paused_id}")

    print("\n2. hook interrupt (BeforeToolCallEvent) on `assign` — approved")
    LOG.clear()
    assign_input = {"resident_id": "r05", "volunteer_id": "v1"}
    agent = build([Turn(tool_calls=[ToolCall("assign", assign_input, "tu-2")])])
    result = agent("assign r05")
    ok &= check("stop_reason at pause", result.stop_reason, "interrupt")
    ok &= check("interrupt name", result.interrupts[0].name, "spike-assign")
    ok &= check("tool body did NOT run at pause", LOG, ["assign:hook"])

    result = agent(respond(result, "approve"))
    ok &= check("stop_reason after approve", result.stop_reason, "end_turn")
    ok &= check(
        "tool body ran exactly once after approve",
        LOG,
        ["assign:hook", "assign:hook", "assign:body:r05:v1"],
    )

    print("\n3. hook interrupt on `assign` — declined")
    LOG.clear()
    agent = build([Turn(tool_calls=[ToolCall("assign", assign_input, "tu-3")])])
    result = agent("assign r05")
    result = agent(respond(result, "handle"))
    ok &= check("tool body never ran", [e for e in LOG if e.startswith("assign:body")], [])
    ok &= check("stop_reason after decline", result.stop_reason, "end_turn")

    print("\n4. interrupt id is derived from toolUseId + name (stable across processes)")
    from uuid import NAMESPACE_OID, uuid5

    ok &= check(
        "id formula", paused_id, f"v1:tool_call:tu-1:{uuid5(NAMESPACE_OID, 'spike-escalate')}"
    )

    print("\nSPIKE A:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
