"""Phase 4 spike S4 ($0, offline): does a direct tool call pass through CedarAuthorization?

A live-mode incident places a real call without the dispatcher model deciding to (the phone gate
needs a deterministic trigger). The call must still be decided by Cedar at the tool boundary.
This builds the dispatcher's own agent shape (tools + Cedar intervention + audit hook), then calls
`agent.tool.place_checkin_call(...)` directly for (1) an allowlisted resident and (2) a resident
whose number is random, and prints what Cedar and the audit log said.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

os.environ["CALL_ALLOWLIST"] = "+15035550100"
os.environ["SPIKE_RANDOM_NUMBER"] = "+14155550199"

from strands import Agent  # noqa: E402

from conftest import make_ctx  # noqa: E402
from doorstep_agent.audit import AuditHook  # noqa: E402
from doorstep_agent.policies import build_cedar  # noqa: E402
from doorstep_agent.tools import place_checkin_call  # noqa: E402
from helpers.scripted_model import ScriptedModel, ToolCall, Turn  # noqa: E402


def main() -> int:
    ctx = make_ctx("live")
    ctx.alert_severity = "Extreme"  # take quiet hours out of this spike
    r05 = ctx.store.resident("r05")
    r05.phone_ref = "env:SPIKE_RANDOM_NUMBER"  # the in-memory store hands out shared objects
    for rid in ("r01", "r05"):
        # Direct `agent.tool.x()` calls merge invocation state into the tool input (it fails to
        # serialize the RunContext), so the deterministic trigger is a one-turn model instead.
        model = ScriptedModel(
            [
                Turn(
                    tool_calls=[
                        ToolCall(
                            "place_checkin_call",
                            {"resident_id": rid, "reason": "spike"},
                            f"t-{rid}",
                        )
                    ]
                )
            ]
        )
        agent = Agent(
            name="spike",
            model=model,
            tools=[place_checkin_call],
            interventions=[build_cedar(ctx)],
            hooks=[AuditHook("agent:dispatcher")],
            callback_handler=None,
        )
        n0 = len(ctx.store.events(ctx.incident_id))
        agent("place the call", invocation_state=ctx.invocation_state())
        for m in agent.messages:
            for b in m.get("content", []):
                if "toolResult" in b:
                    print(rid, "tool result:", b["toolResult"]["content"])
        for e in ctx.store.events(ctx.incident_id)[n0:]:
            print("   audit:", e.actor, e.type, e.tool, e.policy_decision, (e.reason or "")[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
