"""Gate 2's real test: a decision survives the process that raised it.

An interrupt is raised in one interpreter, which then exits. A second interpreter — no shared
memory, no agent object, nothing but the session directory and the decision record — answers it.
The volunteer task must be sent exactly once, and a second tap must change nothing.

This runs offline (scripted model) so it belongs in `make test` and in CI, where a regression in
the resume path would otherwise only show up in a live drill.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HALF = Path(__file__).parent / "helpers" / "restart_half.py"
CAPTAIN_CHAT = "42424242"


def run_half(half: str, work: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        # The roster stores chat ids as env references; the captain's resolves to this one, so
        # half B's responder is recognised as cap-maria and nobody else.
        "TELEGRAM_CAPTAIN_CHAT_ID": CAPTAIN_CHAT,
        "TELEGRAM_VOLUNTEER_CHAT_IDS": "51515151,52525252",
    }
    return subprocess.run(
        [sys.executable, str(HALF), half, str(work)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_interrupt_survives_a_restart_and_the_tool_runs_exactly_once(tmp_path: Path) -> None:
    first = run_half("a", tmp_path)
    assert first.returncode == 0, f"half A failed:\n{first.stdout}\n{first.stderr}"
    assert "HALF A: paused" in first.stdout

    second = run_half("b", tmp_path)
    assert second.returncode == 0, f"half B failed:\n{second.stdout}\n{second.stderr}"

    # The captain's answer was applied in the second process...
    assert "HALF B: applied" in second.stdout
    # ...and the same tap again was recognised, not replayed.
    assert "HALF B AGAIN: already_answered" in second.stdout

    sent = (tmp_path / "outbox.tsv").read_text(encoding="utf-8").splitlines()
    tasks = [line for line in sent if "\tvolunteer_task\t" in line]
    assert len(tasks) == 1, f"the volunteer task must be sent exactly once, got: {tasks}"
    assert tasks[0].startswith("B\t"), "it must be sent by the process that got the approval"
    assert "vol-tom" in tasks[0]


def test_the_paused_session_is_on_disk_between_the_two_processes(tmp_path: Path) -> None:
    assert run_half("a", tmp_path).returncode == 0
    snapshots = list((tmp_path / "sessions").rglob("snapshot_latest.json"))
    assert snapshots, "the paused agent must be persisted, or nothing could resume it"
    saved = snapshots[0].read_text(encoding="utf-8")
    assert "doorstep-approve-door-knock" in saved
    assert '"activated": true' in saved
