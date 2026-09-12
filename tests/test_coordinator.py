"""The cloud coordinator, offline: the same events AgentCore delivers, on DynamoDB (moto).

The headline test is the Gate 3 resume path without the network: a check-in event makes the real
dispatcher (scripted model, real tools, hooks, Cedar and sessions) pause for the captain; a
*different* coordinator — a new process as far as the store is concerned — receives the tap and
carries it out once; the same tap delivered again changes nothing.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from conftest import DATA, SUBSET, make_ctx, new_backend
from doorstep_agent.cloud import coordinator as coordinator_module
from doorstep_agent.cloud.coordinator import Coordinator, runtime_session_id
from doorstep_agent.config import settings
from doorstep_agent.store_dynamo import DynamoBackend
from helpers.agent_harness import ToolCall, run_script

CAPTAIN_CHAT = "42424242"


class Flags:
    def __init__(self, killed: bool = False) -> None:
        self.killed = killed

    def kill_switch(self) -> bool:
        return self.killed


class Tracker:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished = 0

    def start(self, name: str) -> str:
        self.started.append(name)
        return name

    def done(self, token: Any) -> None:
        self.finished += 1


@pytest.fixture(autouse=True)
def chats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CAPTAIN_CHAT_ID", CAPTAIN_CHAT)
    monkeypatch.setenv("TELEGRAM_VOLUNTEER_CHAT_IDS", "51515151,52525252")


def coordinator(tmp_path: Path, *, model: Any = None, **kw: Any) -> Coordinator:
    cfg = replace(settings(), sessions_dir=tmp_path / "sessions")
    return Coordinator(
        new_backend(), flags=kw.pop("flags", Flags()), cfg=cfg, model_override=model, **kw
    )


def tap(option: str, *, decision: str, update_id: int, chat: str = CAPTAIN_CHAT) -> dict[str, Any]:
    return {
        "type": "decision_response",
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": int(chat)},
            "message": {"message_id": 7, "chat": {"id": int(chat)}},
            "data": f"d|inc-test|{decision}|{option}",
        },
    }


def audit(backend: DynamoBackend) -> list[Any]:
    return backend.for_incident("inc-test").events("inc-test")


async def test_a_pause_in_one_process_is_answered_once_by_another(aws, tmp_path: Path) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))  # seed the incident
    model = run_script(
        ToolCall(
            "assign_volunteer",
            {
                "resident_id": "r04",
                "volunteer_id": "vol-tom",
                "include_brief": True,
                "reason": "Needs a ride to a cooling centre.",
            },
            "tu-cloud",
        ),
        final_text="assigned",
    )

    first = coordinator(tmp_path, model=model)
    accepted = await first.handle(
        {
            "incident_id": "inc-test",
            "event": {
                "type": "checkin_result",
                "resident_id": "r04",
                "attempt_key": "a1",
                "result": {"status": "NEEDS_HELP", "needs": ["ride"], "summary": "needs a ride"},
            },
        }
    )
    assert accepted["accepted"] is True
    await first.drain()

    reader = new_backend().for_incident("inc-test", SUBSET)
    pending = reader.decisions("inc-test", status="pending")
    assert [d.name for d in pending] == ["doorstep-approve-door-knock"]
    assert pending[0].interrupt_id and pending[0].session_id

    second = coordinator(tmp_path, model=model)  # new identity map: a new process
    for update_id in (1, 1, 2):  # a Telegram retry of update 1, then a genuine second tap
        await second.handle(
            {
                "incident_id": "inc-test",
                "event": tap("approve", decision=pending[0].id, update_id=update_id),
            }
        )
        await second.drain()

    final = new_backend().for_incident("inc-test", SUBSET)
    tasks = [m for m in final.messages("inc-test") if m.kind == "volunteer_task"]
    assert len(tasks) == 1 and tasks[0].recipient == "vol-tom"
    assert final.case("inc-test", "r04").assigned_volunteer == "vol-tom"
    decision = final.decision(pending[0].id)
    assert decision.status == "answered" and decision.responder == "captain:cap-maria"
    assert decision.applied_at is not None
    outcomes = [
        e.data.get("outcome")
        for e in audit(new_backend())
        if e.data.get("event") == "decision_response"
    ]
    assert outcomes == ["applied", "already_answered", "already_answered"]


async def test_a_check_in_delivered_twice_is_applied_once(aws, tmp_path: Path) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    model = run_script(final_text="nothing to do")
    c = coordinator(tmp_path, model=model)
    event = {
        "incident_id": "inc-test",
        "event": {
            "type": "checkin_result",
            "resident_id": "r06",
            "attempt_key": "k",
            "result": {"status": "OK"},
        },
    }
    assert (await c.handle(event))["accepted"] is True
    again = await c.handle(event)
    await c.drain()
    assert again["accepted"] is False
    assert new_backend().for_incident("inc-test", SUBSET).case("inc-test", "r06").attempts == 1


async def test_a_replayed_start_runs_one_drill(aws, tmp_path: Path) -> None:
    runs: list[dict[str, Any]] = []

    class FakeRunner:
        ctx = None

        def __init__(self, **kwargs: Any) -> None:
            runs.append(kwargs)

        async def run(self) -> Any:
            class Report:
                all_settled, urgent_escalated, policy_violations, wall_seconds = True, [], 0, 1.0

            return Report()

    tracker = Tracker()
    c = coordinator(tmp_path, runner_factory=FakeRunner, tracker=tracker)
    start = {
        "incident_id": "drill-20260912-190501-a1b2",
        "event": {"type": "replay", "telegram": False},
    }
    assert (await c.handle(start))["accepted"] is True
    assert (await c.handle(start))["accepted"] is False
    await c.drain()
    assert len(runs) == 1 and runs[0]["incident_id"] == "drill-20260912-190501-a1b2"
    assert tracker.started == ["drill:drill-20260912-190501-a1b2"] and tracker.finished == 1


async def test_the_kill_switch_refuses_everything_that_spends(aws, tmp_path: Path) -> None:
    c = coordinator(tmp_path, flags=Flags(killed=True))
    for kind in ("replay", "checkin_result", "decision_response", "alert"):
        result = await c.handle({"incident_id": "inc-test", "event": {"type": kind}})
        assert result["ok"] is False and "kill switch" in result["error"]


async def test_malformed_events_are_refused(aws, tmp_path: Path) -> None:
    c = coordinator(tmp_path)
    assert (await c.handle({"incident_id": "../etc", "event": {"type": "status"}}))["ok"] is False
    assert (await c.handle({"incident_id": "inc-test", "event": {"type": "rm -rf"}}))["ok"] is False
    assert (await c.handle({"incident_id": "inc-test", "event": {"type": "checkin_result"}}))[
        "ok"
    ] is False


async def test_an_alert_outside_the_profile_never_starts_anything(aws, tmp_path: Path) -> None:
    c = coordinator(tmp_path)
    feature = {"properties": {"id": "x", "event": "Wind Advisory", "severity": "Minor"}}
    result = await c.handle(
        {"incident_id": "alert-x-0001", "event": {"type": "alert", "feature": feature}}
    )
    assert result["accepted"] is False


def test_runtime_session_ids_meet_the_api_constraints() -> None:
    import re

    for incident in ("inc-test", "drill-20260912-190501-a1b2", "x" * 60):
        sid = runtime_session_id(incident)
        assert 33 <= len(sid) <= 100 and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-_]*", sid)
    assert runtime_session_id("inc-test") == runtime_session_id("inc-test")
    assert runtime_session_id("inc-a") != runtime_session_id("inc-b")


def test_boot_id_is_per_process() -> None:
    assert len(coordinator_module.BOOT_ID) == 12
    assert json.dumps({"b": coordinator_module.BOOT_ID})
    assert DATA.exists()
