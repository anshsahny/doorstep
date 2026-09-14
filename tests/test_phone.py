"""The phone path offline: who can be rung, and a whole call with a fake Twilio.

The rule under test is CLAUDE.md's: a real call reaches only a number on the allowlist, enforced
in Cedar **and** in code on the call path. Here that is checked four ways:

* Cedar denies a non-allowlisted callee, and waives quiet hours only for the operator's own number
  on an incident flagged `operator_test`;
* `place_checkin_call` refuses in code and never enqueues;
* `checkin_worker`, the last step before Twilio, re-derives everything and dials only numbers on
  the allowlist (a property test over hundreds of hostile jobs), never from a parent account,
  never twice for one job;
* nothing but `twilio_rest.py` can create a call.

Then one call end to end: live incident → Cedar → queue → worker (fake Twilio) → TwiML token →
phone bridge on a fake Twilio socket → a red flag pages the captain before the line closes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import boto3
import pytest
from strands.agent.state import AgentState
from strands.hooks import BeforeToolCallEvent
from strands.interventions import Proceed
from test_voice import FakeAgent, end_call, transcript

from conftest import ROOT, SUBSET, TABLE, make_ctx, new_backend
from doorstep_agent.cloud.coordinator import Coordinator
from doorstep_agent.config import settings
from doorstep_agent.models import CaseState
from doorstep_agent.policies import build_cedar
from doorstep_agent.runtime import RunContext, normalize_number
from doorstep_agent.tools import place_checkin_call
from doorstep_api import checkin_worker
from doorstep_api.common import Deps
from doorstep_api.common import normalize_number as api_normalize_number
from doorstep_voice import tokens
from doorstep_voice.audio import pcm16_to_ulaw
from doorstep_voice.phone import serve_phone
from doorstep_voice.serve import VoiceDeps
from doorstep_voice.sink import page_is_out
from helpers.agent_harness import ToolCall, run_script

OPERATOR = "+15550000001"
SECRET = "voice-secret-for-tests"
SUBACCOUNT = "AC" + "1" * 32
PARENT = "AC" + "9" * 32


# --- numbers ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+15550000001", "+15550000001"),
        ("+1 (555) 000-0001", "+15550000001"),
        ("+1.555.000.0001", "+15550000001"),
        ("15550000001", None),
        ("+0555000000", None),
        ("+1555", None),
        ("tel:+15550000001", None),
        ("+15550000001;ext=2", None),
        ("", None),
        (None, None),
    ],
)
def test_both_number_normalizers_agree(raw: str | None, expected: str | None) -> None:
    assert normalize_number(raw) == expected == api_normalize_number(raw)


# --- Cedar: quiet hours and the operator's own number ----------------------------------------


def decide(ctx: RunContext, resident: str = "r01") -> tuple[bool, dict[str, Any]]:
    state = ctx.invocation_state()
    event = BeforeToolCallEvent(
        agent=type("A", (), {"state": AgentState()})(),
        selected_tool=None,
        tool_use={
            "toolUseId": "t",
            "name": "place_checkin_call",
            "input": {"resident_id": resident, "reason": "x"},
        },  # noqa: E501
        invocation_state=state,
    )
    return isinstance(build_cedar(ctx).before_tool_call(event), Proceed), state["_cedar_session"]


@pytest.fixture
def night(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 23)
    monkeypatch.setenv("CALL_ALLOWLIST", f"{OPERATOR},+15550000002")
    monkeypatch.setenv("OPERATOR_TEST_NUMBER", "+1 (555) 000-0001")


def live_ctx(*, operator_test: bool, mode: str = "live") -> RunContext:
    ctx = make_ctx(mode)
    ctx.alert_severity = "Severe"
    ctx.store.incident(ctx.incident_id).run_options["operator_test"] = operator_test
    return ctx


def test_quiet_hours_are_waived_only_for_the_operators_number_on_an_operator_test(night) -> None:
    ok, session = decide(live_ctx(operator_test=True))
    assert ok and session["operator_test_call"] is True and session["local_hour"] == 23


def test_quiet_hours_still_bind_an_ordinary_live_incident(night) -> None:
    ok, session = decide(live_ctx(operator_test=False))
    assert not ok and session["operator_test_call"] is False


def test_the_flag_does_not_exempt_any_other_allowlisted_number(night, monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_TEST_NUMBER", "+15550000002")  # r01 resolves to ...0001
    ok, session = decide(live_ctx(operator_test=True))
    assert (
        not ok and session["callee_allowlisted"] is True and session["operator_test_call"] is False
    )


def test_the_flag_changes_nothing_outside_live_mode(night) -> None:
    for mode in ("drill", "sandbox"):
        ok, _ = decide(live_ctx(operator_test=True, mode=mode))
        assert not ok, mode


def test_a_random_number_is_denied_by_cedar_day_or_night(monkeypatch) -> None:
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 14)
    monkeypatch.setenv("CALL_ALLOWLIST", OPERATOR)
    monkeypatch.setenv("OPERATOR_TEST_NUMBER", OPERATOR)
    rng = random.Random(7)
    for _ in range(25):
        monkeypatch.setenv("RANDOM_CALLEE", f"+1{rng.randint(2000000000, 9999999999)}")
        ctx = live_ctx(operator_test=True)
        ctx.store.resident("r02").phone_ref = "env:RANDOM_CALLEE"
        ok, session = decide(ctx, "r02")
        assert not ok and session["callee_allowlisted"] is False


# --- the tool, in code ------------------------------------------------------------------------


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def enqueue(self, job: dict[str, Any]) -> None:
        self.jobs.append(job)


class _TC:
    def __init__(self, ctx: RunContext) -> None:
        self.invocation_state = ctx.invocation_state(actor="agent:outreach")
        self.tool_use = {"toolUseId": "t"}


def test_the_tool_queues_an_allowlisted_call_once_and_never_names_the_number(monkeypatch) -> None:
    monkeypatch.setenv("CALL_ALLOWLIST", OPERATOR)
    ctx = live_ctx(operator_test=False)
    ctx.call_queue = FakeQueue()
    first = place_checkin_call(resident_id="r01", reason="x", tool_context=_TC(ctx))
    again = place_checkin_call(resident_id="r01", reason="x", tool_context=_TC(ctx))
    assert "queued" in first and "already queued" in again
    assert ctx.call_queue.jobs == [{"incident_id": "inc-test", "resident_id": "r01", "attempt": 1}]


def test_the_tool_refuses_random_numbers_other_modes_and_a_missing_dialer(monkeypatch) -> None:
    monkeypatch.setenv("CALL_ALLOWLIST", OPERATOR)
    monkeypatch.setenv("RANDOM_CALLEE", "+14155550199")
    ctx = live_ctx(operator_test=True)
    ctx.call_queue = FakeQueue()
    ctx.store.resident("r02").phone_ref = "env:RANDOM_CALLEE"
    assert "not on the call allowlist" in place_checkin_call(
        resident_id="r02", reason="x", tool_context=_TC(ctx)
    )
    drill = make_ctx("drill")
    drill.call_queue = FakeQueue()
    assert "never placed in drill" in place_checkin_call(
        resident_id="r01", reason="x", tool_context=_TC(drill)
    )
    no_dialer = live_ctx(operator_test=False)
    assert "no dialer" in place_checkin_call(
        resident_id="r01", reason="x", tool_context=_TC(no_dialer)
    )
    assert ctx.call_queue.jobs == [] and drill.call_queue.jobs == []


# --- the dialer -------------------------------------------------------------------------------


class FakeTwilio:
    def __init__(self, *, sid: str = SUBACCOUNT, owner: str = PARENT) -> None:
        self.sid, self.owner = sid, owner
        self.calls: list[dict[str, Any]] = []

    def account(self) -> dict[str, Any]:
        return {"sid": self.sid, "owner_account_sid": self.owner, "status": "active"}

    def create_call(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"sid": f"CA{len(self.calls):032d}"}


def seed_live(dynamodb: Any, table: str, incident: str, *, mode: str = "live", residents=("r01",)):
    doc = {"id": incident, "org_id": "juniper-court", "mode": mode, "resident_ids": list(residents)}
    dynamodb.put_item(
        TableName=table,
        Item={"PK": {"S": f"INC#{incident}"}, "SK": {"S": "META"}, "doc": {"S": json.dumps(doc)}},
    )


@pytest.fixture
def worker(aws) -> Deps:
    ssm = boto3.client("ssm")
    for name, value in {
        "kill_switch": "off",
        "call_allowlist": f"{OPERATOR},+15550000002",
        "twilio/subaccount_sid": SUBACCOUNT,
        "twilio/subaccount_token": "token-for-tests",
        "twilio/from_number": "+15550009999",
        "voice_bridge_url": "wss://bridge.example.test/twilio",
        "internal_hmac_secret": SECRET,
    }.items():
        ssm.put_parameter(Name=f"/doorstep/{name}", Value=value, Type="SecureString")
    return Deps(table=TABLE, _ssm=ssm, _dynamodb=boto3.client("dynamodb"))


def job(incident: str = "live-test-1", resident: str = "r01", attempt: int = 1) -> dict[str, Any]:
    return {"incident_id": incident, "resident_id": resident, "attempt": attempt}


def test_the_worker_dials_the_allowlisted_resident_once_with_a_phone_token(worker: Deps) -> None:
    seed_live(worker.dynamodb, TABLE, "live-test-1")
    twilio = FakeTwilio()
    first = checkin_worker.dial(worker, job(), twilio)
    again = checkin_worker.dial(worker, job(), twilio)
    assert first["dialled"] is True and again == {
        "dialled": False,
        "reason": "this call was already dialled",
    }
    assert len(twilio.calls) == 1
    call = twilio.calls[0]
    assert call["to"] == OPERATOR and call["from_"] == "+15550009999" and call["time_limit"] == 300
    twiml = call["twiml"]
    assert '<Stream url="wss://bridge.example.test/twilio">' in twiml and twiml.endswith(
        "<Hangup/></Response>"
    )
    token = re.search(r'name="token" value="([^"]+)"', twiml).group(1)
    claims = tokens.verify(token, SECRET, channel="phone")
    assert (claims["inc"], claims["res"], claims["mode"]) == ("live-test-1", "r01", "live")


def test_a_twilio_failure_is_logged_for_the_alarm_and_never_retried(worker: Deps, capsys) -> None:
    """Phase 6 hardening: the failed call is visible (log metric filter + alarm), not retried."""
    from doorstep_api.twilio_rest import TwilioError

    class Failing(FakeTwilio):
        def create_call(self, **kwargs: Any) -> dict[str, Any]:
            raise TwilioError("Twilio POST failed: HTTP 503")

    seed_live(worker.dynamodb, TABLE, "live-test-1")
    record = {"body": json.dumps(job())}
    out = checkin_worker.handler({"Records": [record]}, deps=worker, twilio=Failing())
    assert out["batchItemFailures"] == [] and out["results"][0]["dialled"] is False
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert any(x.get("msg") == "call failed" for x in lines)


@pytest.mark.parametrize(
    "setup, reason",
    [
        (lambda d: seed_live(d.dynamodb, TABLE, "live-test-1", mode="drill"), "live only"),
        (lambda d: seed_live(d.dynamodb, TABLE, "live-test-1", mode="sandbox"), "live only"),
        (
            lambda d: seed_live(d.dynamodb, TABLE, "live-test-1", residents=("r04",)),
            "not in this incident",
        ),  # noqa: E501
        (lambda d: None, "no such incident"),
    ],
)
def test_the_worker_refuses_anything_but_a_live_incident_covering_the_resident(
    worker: Deps, setup: Any, reason: str
) -> None:
    setup(worker)
    twilio = FakeTwilio()
    result = checkin_worker.dial(worker, job(), twilio)
    assert result["dialled"] is False and reason in result["reason"] and twilio.calls == []


def test_the_worker_refuses_a_parent_account_the_kill_switch_and_a_bad_bridge(worker: Deps) -> None:
    seed_live(worker.dynamodb, TABLE, "live-test-1")
    parent = FakeTwilio(sid=SUBACCOUNT, owner=SUBACCOUNT)  # an account that owns itself
    assert "not a Twilio subaccount" in checkin_worker.dial(worker, job(), parent)["reason"]
    assert parent.calls == []
    worker.ssm.put_parameter(
        Name="/doorstep/voice_bridge_url",
        Value="wss://x.test/twilio?token=1",
        Type="String",
        Overwrite=True,
    )
    worker._params.clear()
    assert "no query string" in checkin_worker.dial(worker, job(attempt=2), FakeTwilio())["reason"]
    worker.ssm.put_parameter(
        Name="/doorstep/kill_switch", Value="on", Type="String", Overwrite=True
    )
    worker._params.clear()
    assert (
        checkin_worker.dial(worker, job(attempt=3), FakeTwilio())["reason"] == "kill switch is on"
    )


def test_the_worker_rings_only_allowlisted_numbers_whatever_the_jobs_and_roster_say(
    worker: Deps,
) -> None:
    """300 hostile jobs: random residents, modes, consent, phone references and allowlists."""
    rng = random.Random(2026)
    backend = new_backend()
    twilio = FakeTwilio()
    dialled_numbers: list[tuple[str, set[str]]] = []
    refs = [
        "env:CALL_ALLOWLIST[0]",
        "env:CALL_ALLOWLIST[1]",
        "env:CALL_ALLOWLIST[5]",
        "env:RANDOM_CALLEE",
        "+14155550199",
        "tel:+15550000001",
        None,
        "env:CALL_ALLOWLIST[0] ",
    ]
    for i in range(300):
        allowlist = ",".join(
            rng.choice(
                [
                    OPERATOR,
                    "+15550000002",
                    "garbage",
                    "",
                    f"+1{rng.randint(2000000000, 9999999999)}",
                ]
            )
            for _ in range(rng.randint(0, 3))
        )
        worker.ssm.put_parameter(
            Name="/doorstep/call_allowlist",
            Value=allowlist or "none",
            Type="SecureString",
            Overwrite=True,
        )  # noqa: E501
        worker._params.clear()
        resident_id = f"r{rng.randint(1, 48):02d}"
        store = backend.for_incident("x")
        resident = store.resident(resident_id)
        resident.phone_ref = rng.choice(refs)
        resident.consent.calls = rng.random() < 0.7
        worker.dynamodb.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": "ORG#juniper-court"},
                "SK": {"S": f"RES#{resident_id}"},
                "doc": {"S": resident.model_dump_json()},
            },  # noqa: E501
        )
        incident = f"live-prop-{i}"
        seed_live(
            worker.dynamodb,
            TABLE,
            incident,
            mode=rng.choice(["live", "live", "drill", "sandbox"]),
            residents=(resident_id,),
        )  # noqa: E501
        before = len(twilio.calls)
        checkin_worker.dial(worker, job(incident, rng.choice([resident_id, "r40"])), twilio)
        if len(twilio.calls) > before:
            listed = {n for n in (normalize_number(x) for x in allowlist.split(",")) if n}
            dialled_numbers.append((twilio.calls[-1]["to"], listed))
    assert dialled_numbers, "the property test should include some legitimate calls"
    assert all(number in listed for number, listed in dialled_numbers)


def test_only_twilio_rest_can_create_a_call() -> None:
    offenders = []
    for folder in ("agent", "api", "voice"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if ("Calls.json" in text or "calls.create" in text) and path.name != "twilio_rest.py":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# --- the phone bridge ----------------------------------------------------------------------------


class FakeTwilioSocket:
    """Plays Twilio: connected, start, then 20 ms mu-law frames; echoes marks; records sends."""

    def __init__(self, start: dict[str, Any], *, frames: int = 400) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.inbox.put_nowait(json.dumps({"event": "connected", "protocol": "Call"}))
        self.inbox.put_nowait(json.dumps({"event": "start", "start": start}))
        payload = base64.b64encode(pcm16_to_ulaw(bytes(320))).decode()
        for _ in range(frames):
            self.inbox.put_nowait(json.dumps({"event": "media", "media": {"payload": payload}}))

    async def receive_text(self) -> str:
        if self.closed:
            raise RuntimeError("closed")
        message = await self.inbox.get()
        await asyncio.sleep(0.005)
        return message

    async def send_text(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        if message["event"] == "mark":
            await self.inbox.put(json.dumps({"event": "mark", "mark": message["mark"]}))

    async def close(self) -> None:
        self.closed = True


def start_message(token: str, *, account: str = SUBACCOUNT) -> dict[str, Any]:
    return {
        "streamSid": "MZ" + "0" * 32,
        "callSid": "CA" + "0" * 32,
        "accountSid": account,
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"token": token},
    }


class Flags:
    def kill_switch(self) -> bool:
        return False


class LoopbackSink:
    """The coordinator in the same process, as `CoordinatorSink` reaches it over AgentCore."""

    def __init__(self, coordinator: Coordinator) -> None:
        self.c = coordinator
        self.log: list[str] = []

    async def urgent(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        self.log.append("urgent")
        await self.c.handle(
            {"incident_id": claims["inc"], "event": {"type": "checkin_urgent", **event}}
        )
        await self.c.drain()

    async def attempt(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        self.log.append("attempt")
        await self.c.handle(
            {"incident_id": claims["inc"], "event": {"type": "checkin_attempt", **event}}
        )
        await self.c.drain()

    async def page_delivered(self, claims: dict[str, Any]) -> bool:
        return page_is_out(new_backend(), claims["inc"], claims["res"])


def phone_token(**kw: Any) -> str:
    args = {"incident_id": "inc-test", "resident_id": "r04", "channel": "phone", "mode": "live"}
    return tokens.mint(SECRET, **(args | kw))[0]


@pytest.mark.parametrize(
    "token_kw, account, why",
    [
        ({}, PARENT, "another account"),
        ({"channel": "browser"}, SUBACCOUNT, "a browser token"),
        ({"mode": "drill"}, SUBACCOUNT, "a drill token"),
    ],
)
async def test_the_bridge_refuses_on_start_before_any_model(
    aws, monkeypatch, token_kw, account, why
) -> None:
    monkeypatch.setenv("INTERNAL_HMAC_SECRET", SECRET)
    make_ctx("live", store=new_backend().for_incident("inc-test", SUBSET))
    built: list[Any] = []
    deps = VoiceDeps(
        backend=new_backend(), sink=None, flags=Flags(), agent_factory=lambda s, p: built.append(1)
    )  # type: ignore[arg-type]  # noqa: E501
    socket = FakeTwilioSocket(start_message(phone_token(**token_kw), account=account), frames=5)
    assert await serve_phone(socket, deps, subaccount_sid=SUBACCOUNT) is None, why
    assert socket.closed and built == []


async def test_one_live_call_end_to_end_pages_the_captain_before_the_line_closes(
    aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RunContext, "local_hour", lambda self: 23)
    monkeypatch.setenv("CALL_ALLOWLIST", OPERATOR)
    monkeypatch.setenv("OPERATOR_TEST_NUMBER", OPERATOR)
    monkeypatch.setenv("INTERNAL_HMAC_SECRET", SECRET)
    make_ctx(store=new_backend().for_incident("seed", SUBSET))  # static rows are seeded by `aws`

    # 1. The coordinator: a live operator-test incident at 11 PM; Cedar allows; one job queued.
    queue = FakeQueue()
    cfg = replace(settings(), sessions_dir=tmp_path / "sessions")
    escalate = run_script(
        ToolCall(
            "escalate_to_captain",
            {"resident_id": "r01", "reason": "Dizzy and confused on the call.", "options": []},
            "tu-esc",
        ),  # noqa: E501
        final_text="escalated",
    )
    coordinator = Coordinator(
        new_backend(), flags=Flags(), cfg=cfg, call_queue=queue, model_override=escalate
    )  # noqa: E501
    started = await coordinator.handle(
        {
            "incident_id": "live-test-1",
            "event": {"type": "live_call", "resident_id": "r01", "operator_test": True},
        }  # noqa: E501
    )
    assert started["accepted"] is True and "queued" in started["outcome"], started
    assert queue.jobs == [{"incident_id": "live-test-1", "resident_id": "r01", "attempt": 1}]

    # The same request without the operator flag is refused by Cedar at 11 PM.
    refused = await coordinator.handle(
        {"incident_id": "live-test-2", "event": {"type": "live_call", "resident_id": "r01"}}
    )
    assert "DENIED" in refused["outcome"] and len(queue.jobs) == 1

    # 2. The worker dials it (fake Twilio) with the phone token in the TwiML.
    ssm = boto3.client("ssm")
    for name, value in {
        "kill_switch": "off", "call_allowlist": OPERATOR, "twilio/subaccount_sid": SUBACCOUNT,
        "twilio/subaccount_token": "t", "twilio/from_number": "+15550009999",
        "voice_bridge_url": "wss://bridge.example.test/twilio", "internal_hmac_secret": SECRET,
    }.items():  # fmt: skip
        ssm.put_parameter(Name=f"/doorstep/{name}", Value=value, Type="SecureString")
    twilio = FakeTwilio()
    checkin_worker.handler(
        {"Records": [{"body": json.dumps(queue.jobs[0])}]},
        deps=Deps(table=TABLE, _ssm=ssm, _dynamodb=boto3.client("dynamodb")),
        twilio=twilio,
    )
    assert [c["to"] for c in twilio.calls] == [OPERATOR]
    token = re.search(r'name="token" value="([^"]+)"', twilio.calls[0]["twiml"]).group(1)

    # 3. Twilio connects the answered call to the bridge; the resident says a red flag.
    sink = LoopbackSink(coordinator)
    agent = FakeAgent(
        [
            transcript("assistant", "How are you feeling right now?"),
            transcript("user", "i feel dizzy and confused"),
            0.3,
            transcript("assistant", "I'm letting the team know so someone can check on you."),
            end_call,
        ]
    )
    deps = VoiceDeps(
        backend=new_backend(), sink=sink, flags=Flags(), agent_factory=lambda s, p: agent
    )
    socket = FakeTwilioSocket(start_message(token))
    record = await asyncio.wait_for(serve_phone(socket, deps, subaccount_sid=SUBACCOUNT), 30)

    assert record is not None and record.end_reason == "completed"
    assert record.page_delivered is True, "the line was held until the page was out"
    assert sink.log == ["urgent", "attempt"]
    assert any(m["event"] == "mark" for m in socket.sent) and socket.closed
    case = new_backend().for_incident("live-test-1").case("live-test-1", "r01")
    assert case.state == CaseState.ESCALATED and case.attempt_log[-1].channel == "phone"
    decisions = new_backend().for_incident("live-test-1").decisions("live-test-1")
    assert [d.name for d in decisions] == ["doorstep-urgent-red-flag"]
