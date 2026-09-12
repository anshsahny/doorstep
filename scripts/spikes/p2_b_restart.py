"""Spike B: does an interrupt survive throwing the agent away and rebuilding in a new process?

This is the shape of the Gate 2 test. Half A raises the interrupt and exits; half B is a fresh
interpreter that knows only the session directory and the interrupt id; it responds and the tool
must run exactly once, in that second process.

The side effect is a line appended to an "outbox" file, so double-execution across the restart
would be visible as two lines no matter which process wrote them.

    python p2_b_restart.py            # parent: runs both halves, checks the outbox
    python p2_b_restart.py a <dir>    # half A
    python p2_b_restart.py b <dir>    # half B
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry
from strands.session import SnapshotSessionManager
from strands.storage import LocalFileStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from helpers.scripted_model import ScriptedModel, ToolCall, Turn  # noqa: E402

SESSION_ID = "doorstep-spike-b"
TOOL_USE_ID = "tu-restart-1"


def outbox_path(work: Path) -> Path:
    return work / "outbox.txt"


def send(work: Path, line: str) -> None:
    with outbox_path(work).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


@tool
def assign_volunteer(resident_id: str, volunteer_id: str) -> str:
    """Send a volunteer a task.

    Args:
        resident_id: The resident's id.
        volunteer_id: The volunteer's id.
    """
    send(WORK, f"task to {volunteer_id} for {resident_id} (pid {os.getpid()})")
    return f"task sent to {volunteer_id}"


class ApprovalHook(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "assign_volunteer":
            return
        reason = dict(event.tool_use["input"])
        answer = event.interrupt("doorstep-approve-door-knock", reason=reason)
        if answer != "approve":
            event.cancel_tool = f"captain chose: {answer}"


def build(work: Path) -> Agent:
    return Agent(
        model=ScriptedModel(
            [
                Turn(
                    tool_calls=[
                        ToolCall(
                            "assign_volunteer",
                            {"resident_id": "r05", "volunteer_id": "vol-tom"},
                            TOOL_USE_ID,
                        )
                    ]
                )
            ]
        ),
        tools=[assign_volunteer],
        hooks=[ApprovalHook()],
        session_manager=SnapshotSessionManager(
            session_id=SESSION_ID, storage=LocalFileStorage(str(work / "sessions"))
        ),
        callback_handler=None,
    )


def half_a(work: Path) -> int:
    """Raise the interrupt, record its id, exit. The agent object dies with the process."""
    agent = build(work)
    result = agent("assign vol-tom to r05")
    if result.stop_reason != "interrupt":
        print(f"HALF A FAIL: stop_reason={result.stop_reason}")
        return 1
    interrupt = result.interrupts[0]
    (work / "interrupt.json").write_text(
        json.dumps({"id": interrupt.id, "name": interrupt.name, "reason": interrupt.reason}),
        encoding="utf-8",
    )
    print(f"HALF A: paused on {interrupt.name} id={interrupt.id} pid={os.getpid()}")
    return 0


def half_b(work: Path) -> int:
    """Fresh process: rebuild from the session directory alone, then respond."""
    saved = json.loads((work / "interrupt.json").read_text(encoding="utf-8"))
    agent = build(work)
    result = agent([{"interruptResponse": {"interruptId": saved["id"], "response": "approve"}}])
    print(f"HALF B: stop_reason={result.stop_reason} pid={os.getpid()}")
    return 0 if result.stop_reason == "end_turn" else 1


def parent() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="doorstep-spike-b-") as tmp:
        work = Path(tmp)
        me = [sys.executable, str(Path(__file__).resolve())]
        for half in ("a", "b"):
            run = subprocess.run([*me, half, str(work)], capture_output=True, text=True)
            print(run.stdout.strip() or run.stderr.strip()[-2000:])
            if run.returncode != 0:
                print(f"  FAIL  half {half} exited {run.returncode}")
                return 1

        lines = (
            outbox_path(work).read_text(encoding="utf-8").splitlines()
            if outbox_path(work).exists()
            else []
        )
        print(f"\n  outbox: {lines}")
        ok &= len(lines) == 1
        print(f"  {'PASS' if len(lines) == 1 else 'FAIL'}  tool ran exactly once across restart")

        sessions = sorted(p.name for p in (work / "sessions").rglob("*") if p.is_file())
        print(f"  session files: {sessions[:6]}{' …' if len(sessions) > 6 else ''}")
        ok &= bool(sessions)

    print("\nSPIKE B:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import os

    if len(sys.argv) == 3:
        WORK = Path(sys.argv[2])
        raise SystemExit(half_a(WORK) if sys.argv[1] == "a" else half_b(WORK))
    WORK = Path(".")
    raise SystemExit(parent())
