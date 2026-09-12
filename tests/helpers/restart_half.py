"""One half of the cross-process restart test, run as its own interpreter.

Half A raises the interrupt and exits, taking the agent object with it. Half B knows only what
is on disk — the session directory and the decision record — and answers from there. Neither
half can see the other's memory, which is the point: that is what a captain answering from their
phone twenty minutes later actually looks like.

    python restart_half.py a <work_dir>
    python restart_half.py b <work_dir>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))

from conftest import make_ctx  # noqa: E402
from doorstep_agent.decisions import Responder, respond_to_decision  # noqa: E402
from doorstep_agent.models import CheckinResult, CheckinStatus  # noqa: E402
from doorstep_agent.runtime import RunContext  # noqa: E402
from helpers.agent_harness import ToolCall, invoke, run_script  # noqa: E402

RESIDENT = "r04"  # wave 1, so a door-knock needs the captain
TOOL_USE_ID = "tu-restart"
CAPTAIN_CHAT = "42424242"


def build_ctx(work: Path) -> RunContext:
    """The same incident in both processes, rebuilt from the same data and session directory."""
    ctx = make_ctx(auto_approve=False, sessions_dir=work / "sessions")
    ctx.model_override = run_script(
        ToolCall(
            "assign_volunteer",
            {
                "resident_id": RESIDENT,
                "volunteer_id": "vol-tom",
                "include_brief": True,
                "reason": "No answer after three calls.",
            },
            TOOL_USE_ID,
        ),
        final_text="assigned",
    )
    case = ctx.store.case(ctx.incident_id, RESIDENT)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.NEEDS_HELP, needs=["ride"]))
    return ctx


_recorded = 0


def record(work: Path, ctx: RunContext, label: str) -> None:
    """Append only what happened since the last call, so the parent can count real side effects."""
    global _recorded
    new = ctx.outbox[_recorded:]
    _recorded = len(ctx.outbox)
    lines = [f"{label}\t{m.kind}\t{m.recipient}\t{m.text[:60]}" for m in new]
    with (work / "outbox.tsv").open("a", encoding="utf-8") as fh:
        fh.write("".join(line + "\n" for line in lines))


async def half_a(work: Path) -> int:
    from doorstep_agent.agents.dispatcher import _record_pauses

    ctx = build_ctx(work)
    result = await invoke(ctx, RESIDENT, f"help {RESIDENT}", model=ctx.model_override)
    if result.stop_reason != "interrupt":
        print(f"HALF A FAIL: stop_reason={result.stop_reason}")
        return 1

    # What the runner does when an agent pauses: make the decision answerable, then hand it to a
    # channel. Nothing before this point could have been answered.
    decision = _record_pauses(ctx, RESIDENT, result)[0]
    (work / "handoff.json").write_text(
        json.dumps(
            {
                "decision_id": decision.id,
                "interrupt_id": decision.interrupt_id,
                "session_id": decision.session_id,
                "decision": decision.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )
    record(work, ctx, "A")
    print(f"HALF A: paused {decision.id} interrupt={decision.interrupt_id}")
    return 0


async def half_b(work: Path) -> int:
    """A fresh process: no agent, no context, just the session on disk and the decision record."""
    from doorstep_agent.models import Decision

    handoff = json.loads((work / "handoff.json").read_text(encoding="utf-8"))
    ctx = build_ctx(work)
    ctx.store.save_decision(Decision.model_validate(handoff["decision"]))

    outcome = await respond_to_decision(
        ctx,
        handoff["decision_id"],
        "approve",
        Responder(source="telegram", external_id=CAPTAIN_CHAT),
    )
    record(work, ctx, "B")
    print(f"HALF B: {outcome.kind} | {outcome.message} | {outcome.detail}")

    # Tapping the same button a second time, from the same phone, must change nothing.
    again = await respond_to_decision(
        ctx,
        handoff["decision_id"],
        "approve",
        Responder(source="telegram", external_id=CAPTAIN_CHAT),
    )
    record(work, ctx, "B2")
    print(f"HALF B AGAIN: {again.kind} | {again.message}")
    return 0 if outcome.kind == "applied" and again.kind == "already_answered" else 1


def main() -> int:
    half, work = sys.argv[1], Path(sys.argv[2])
    return asyncio.run(half_a(work) if half == "a" else half_b(work))


if __name__ == "__main__":
    raise SystemExit(main())
