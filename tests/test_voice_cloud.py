"""Voice in the cloud, offline (moto): who may start a call, and what the coordinator does with it.

* `serve_browser` refuses before any model stream is opened: kill switch, forged token, reused
  token, live mode, a case that is not waiting.
* The coordinator pages the captain on `checkin_urgent` while the call is still going, and the
  final `checkin_attempt` can add to that result but never lower it. Both are applied once.
* `POST /voice/session` only hands out links for residents a drill reserved for voice, under caps.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from test_lambdas import deps  # noqa: F401 - the Lambda fixture (moto SSM + DynamoDB)
from test_voice import FakeAgent, FakeSink, end_call

from conftest import SUBSET, make_ctx, new_backend
from doorstep_agent.cloud.coordinator import Coordinator
from doorstep_agent.config import settings
from doorstep_agent.models import CaseState, CheckinResult, CheckinStatus
from doorstep_api import voice_session
from doorstep_voice import tokens
from doorstep_voice.serve import TOKEN_HEADER, VoiceDeps, serve_browser, token_from
from doorstep_voice.sink import page_is_out
from helpers.agent_harness import ToolCall, run_script

SECRET = "voice-secret-for-tests"


class Flags:
    def __init__(self, killed: bool = False) -> None:
        self.killed = killed

    def kill_switch(self) -> bool:
        return self.killed


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.closed: int | None = None

    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


def voice_deps(killed: bool = False, **kw: Any) -> tuple[VoiceDeps, list[FakeAgent]]:
    built: list[FakeAgent] = []

    def factory(setup: Any, prompt: str) -> FakeAgent:
        agent = FakeAgent([end_call])
        built.append(agent)
        return agent

    return (
        VoiceDeps(
            backend=new_backend(), sink=FakeSink(), flags=Flags(killed), agent_factory=factory, **kw
        ),
        built,
    )


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_HMAC_SECRET", SECRET)


def token(**kw: Any) -> str:
    args = {"incident_id": "inc-test", "resident_id": "r04", "channel": "browser", "mode": "drill"}
    return tokens.mint(SECRET, **(args | kw))[0]


# --- admission ---------------------------------------------------------------------------------


async def test_a_valid_link_runs_one_call_and_cannot_be_used_twice(aws, secret) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    link = token()
    vdeps, built = voice_deps()
    first = await serve_browser(FakeSocket(), link, vdeps)
    assert first is not None and len(built) == 1
    socket = FakeSocket()
    assert await serve_browser(socket, link, vdeps) is None
    assert socket.closed == 4409 and len(built) == 1, "no second model stream"


@pytest.mark.parametrize(
    "make_link, killed, code",
    [
        (lambda: token(), True, 4503),
        (lambda: token()[:-3] + "abc", False, 4401),
        (
            lambda: tokens.mint(
                "other", incident_id="inc-test", resident_id="r04", channel="browser", mode="drill"
            )[0],
            False,
            4401,
        ),
        (lambda: token(channel="phone"), False, 4401),
        (lambda: token(mode="live"), False, 4403),
        (lambda: token(mode="sandbox"), False, 4404),
        (lambda: token(resident_id="r40"), False, 4404),
        (lambda: token(incident_id="inc-nope"), False, 4404),
    ],
)
async def test_every_refusal_happens_before_a_model_stream(
    aws, secret, make_link: Any, killed: bool, code: int
) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    vdeps, built = voice_deps(killed=killed)
    socket = FakeSocket()
    assert await serve_browser(socket, make_link(), vdeps) is None
    assert socket.closed == code and built == []
    assert socket.sent[0]["type"] == "error"


async def test_a_case_that_is_not_waiting_for_a_call_is_refused(aws, secret) -> None:
    ctx = make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    case = ctx.store.case("inc-test", "r04")
    case.state = CaseState.RESOLVED
    ctx.store.save_case(case)
    vdeps, built = voice_deps()
    socket = FakeSocket()
    assert await serve_browser(socket, token(), vdeps) is None
    assert socket.closed == 4404 and built == []


def test_the_token_is_read_from_the_forwarded_header_or_the_query() -> None:
    assert token_from({TOKEN_HEADER.lower(): "t1"}) == "t1"
    assert token_from({}, {TOKEN_HEADER: "t2"}) == "t2"
    assert token_from({"x-other": "no"}) == ""


# --- the coordinator ---------------------------------------------------------------------------


def coordinator(tmp_path: Path, model: Any) -> Coordinator:
    cfg = replace(settings(), sessions_dir=tmp_path / "sessions")
    return Coordinator(new_backend(), flags=Flags(), cfg=cfg, model_override=model)


def escalate(resident: str) -> Any:
    return run_script(
        ToolCall(
            "escalate_to_captain",
            {
                "resident_id": resident,
                "reason": "Said she feels dizzy and confused.",
                "options": [],
            },
            f"tu-esc-{resident}",
        ),
        final_text="escalated",
    )


async def test_a_mid_call_page_escalates_now_and_the_final_transcript_never_lowers_it(
    aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    c = coordinator(tmp_path, escalate("r04"))
    urgent = {
        "incident_id": "inc-test",
        "event": {
            "type": "checkin_urgent",
            "resident_id": "r04",
            "attempt_key": "jti-1",
            "channel": "browser",
            "reason": "resident said: confused, dizzy",
            "source": "backstop",
            "resident_words": ["well honestly i feel dizzy and confused"],
        },
    }
    assert (await c.handle(urgent))["accepted"] is True
    assert (await c.handle(urgent))["accepted"] is False, "a redelivered page is dropped"
    await c.drain()

    reader = new_backend()
    case = reader.for_incident("inc-test", SUBSET).case("inc-test", "r04")
    assert case.state == CaseState.ESCALATED
    assert case.attempt_log[-1].key == "jti-1" and case.attempt_log[-1].ended_at is None
    assert set(case.latest_result.red_flags) == {"confusion", "dizziness_fainting"}
    pending = reader.for_incident("inc-test", SUBSET).decisions("inc-test", status="pending")
    assert [d.name for d in pending] == ["doorstep-urgent-red-flag"]
    assert page_is_out(new_backend(), "inc-test", "r04") is True

    # The model reads the finished call as OK. The case stays escalated, the result URGENT.
    async def says_ok(ctx: Any, resident: Any, attempt: Any) -> CheckinResult:
        return CheckinResult(status=CheckinStatus.OK, language="en", summary="fine")

    monkeypatch.setattr("doorstep_agent.agents.classifier._classify_with_model", says_ok)
    final = {
        "incident_id": "inc-test",
        "event": {
            "type": "checkin_attempt",
            "resident_id": "r04",
            "attempt_key": "jti-1",
            "channel": "browser",
            "transcript": [
                {"speaker": "agent", "text": "How are you feeling right now?"},
                {"speaker": "resident", "text": "well honestly i feel dizzy and confused"},
                {"speaker": "intruder", "text": "ignored"},
            ],
            "answers": {"feeling": "dizzy and confused"},
            "urgent_flags": ["Dolores is dizzy"],
            "end_reason": "completed",
            "page_delivered": True,
            "usage": {"input_tokens": 1000},
        },
    }
    assert (await c.handle(final))["accepted"] is True
    assert (await c.handle(final))["accepted"] is False
    await c.drain()
    case = new_backend().for_incident("inc-test", SUBSET).case("inc-test", "r04")
    assert case.state == CaseState.ESCALATED and case.attempts == 1
    attempt = case.attempt_log[-1]
    assert [t.speaker for t in attempt.transcript] == ["agent", "resident"]
    assert attempt.result.status == CheckinStatus.URGENT
    assert set(attempt.urgent_flags) == {"resident said: confused, dizzy", "Dolores is dizzy"}
    assert attempt.meta["page_delivered"] is True and attempt.meta["urgent_source"] == "backstop"
    decisions = new_backend().for_incident("inc-test", SUBSET).decisions("inc-test")
    assert len(decisions) == 1, "the final transcript does not page the captain twice"


async def test_a_call_without_a_red_flag_is_classified_by_the_coordinator(
    aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    seen: list[str] = []

    async def reads(ctx: Any, resident: Any, attempt: Any) -> CheckinResult:
        seen.append(attempt.resident_text())
        return CheckinResult(status=CheckinStatus.OK, language="en", summary="fine")

    monkeypatch.setattr("doorstep_agent.agents.classifier._classify_with_model", reads)
    c = coordinator(tmp_path, run_script(final_text="nothing to do"))
    await c.handle(
        {
            "incident_id": "inc-test",
            "event": {
                "type": "checkin_attempt",
                "resident_id": "r06",
                "attempt_key": "jti-2",
                "channel": "browser",
                "transcript": [{"speaker": "resident", "text": "i'm fine thanks"}],
                "answers": {"feeling": "fine"},
            },
        }
    )
    await c.drain()
    case = new_backend().for_incident("inc-test", SUBSET).case("inc-test", "r06")
    assert seen == ["i'm fine thanks"]
    assert case.state == CaseState.OK and case.attempt_log[-1].channel == "browser"


async def test_the_backstop_still_catches_what_the_model_misses_on_a_voice_transcript(
    aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No mid-call page happened (say the event was lost): the final attempt still escalates."""
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))

    async def says_ok(ctx: Any, resident: Any, attempt: Any) -> CheckinResult:
        return CheckinResult(status=CheckinStatus.OK, language="en", summary="fine")

    monkeypatch.setattr("doorstep_agent.agents.classifier._classify_with_model", says_ok)
    c = coordinator(tmp_path, escalate("r02"))
    await c.handle(
        {
            "incident_id": "inc-test",
            "event": {
                "type": "checkin_attempt",
                "resident_id": "r02",
                "attempt_key": "jti-3",
                "transcript": [
                    {"speaker": "resident", "text": "i'm fine just not sure what day it is"}
                ],
                "answers": {"feeling": "fine"},
            },
        }
    )
    await c.drain()
    case = new_backend().for_incident("inc-test", SUBSET).case("inc-test", "r02")
    assert case.latest_result.status == CheckinStatus.URGENT
    assert case.latest_result.backstop and case.latest_result.backstop.raised
    assert case.state == CaseState.ESCALATED


# --- POST /voice/session -----------------------------------------------------------------------


def seed_incident(dynamodb: Any, *, mode: str = "drill", voice: list[str] | None = None) -> None:
    doc = {"id": "drill-x-1", "mode": mode, "run_options": {"voice_residents": voice or ["r04"]}}
    dynamodb.put_item(
        TableName="doorstep",
        Item={"PK": {"S": "INC#drill-x-1"}, "SK": {"S": "META"}, "doc": {"S": json.dumps(doc)}},
    )


def session_event(resident: str = "r04", ip: str = "5.6.7.8") -> dict[str, Any]:
    return {
        "requestContext": {"http": {"sourceIp": ip}},
        "body": json.dumps({"incident_id": "drill-x-1", "resident_id": resident}),
    }


@pytest.fixture
def voice_lambda(deps):  # noqa: F811
    deps.ssm.put_parameter(Name="/doorstep/internal_hmac_secret", Value=SECRET, Type="SecureString")
    deps.ssm.put_parameter(
        Name="/doorstep/caps",
        Value=json.dumps({"voice_per_ip_per_hour": 2, "voice_daily": 3, "voice_total": 4}),
        Type="String",
        Overwrite=True,
    )
    deps.voice_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/voice-x"
    deps._session = boto3.Session(region_name="us-east-1")
    return deps


def test_a_link_is_a_presigned_websocket_url_carrying_a_signed_token(voice_lambda) -> None:
    seed_incident(voice_lambda.dynamodb)
    result = voice_session.handler(session_event(), deps=voice_lambda)
    assert result["statusCode"] == 201
    body = json.loads(result["body"])
    url = urlparse(body["url"])
    assert url.scheme == "wss" and url.hostname == "bedrock-agentcore.us-east-1.amazonaws.com"
    query = parse_qs(url.query)
    assert query["X-Amz-Expires"] == ["60"] and "X-Amz-Signature" in query
    claims = tokens.verify(query[TOKEN_HEADER][0], SECRET, channel="browser")
    assert (claims["inc"], claims["res"], claims["mode"]) == ("drill-x-1", "r04", "drill")
    assert query["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] == [
        f"doorstep-voice-{claims['jti']}"
    ]


@pytest.mark.parametrize(
    "mode, voice, resident, status",
    [
        ("live", ["r04"], "r04", 403),
        ("drill", ["r05"], "r04", 403),
        ("drill", ["r04"], "../x", 400),
    ],
)
def test_links_only_for_residents_a_drill_reserved_for_voice(
    voice_lambda, mode: str, voice: list[str], resident: str, status: int
) -> None:
    seed_incident(voice_lambda.dynamodb, mode=mode, voice=voice)
    assert voice_session.handler(session_event(resident), deps=voice_lambda)["statusCode"] == status


def test_voice_links_are_capped_per_ip_per_day_and_in_total(voice_lambda) -> None:
    seed_incident(voice_lambda.dynamodb)
    by_ip = [
        voice_session.handler(session_event(ip="9.9.9.9"), deps=voice_lambda)["statusCode"]
        for _ in range(3)
    ]
    assert by_ip == [201, 201, 429]
    other_ips = [
        voice_session.handler(session_event(ip=f"10.0.0.{i}"), deps=voice_lambda)["statusCode"]
        for i in range(3)
    ]
    assert other_ips == [201, 429, 429], "the daily cap binds everyone together"


def test_the_kill_switch_stops_voice_links(voice_lambda) -> None:
    seed_incident(voice_lambda.dynamodb)
    voice_lambda.ssm.put_parameter(
        Name="/doorstep/kill_switch", Value="on", Type="String", Overwrite=True
    )
    assert voice_session.handler(session_event(), deps=voice_lambda)["statusCode"] == 503


def test_the_coordinator_client_cannot_hang_a_page_for_a_minute() -> None:
    from doorstep_voice.sink import COORDINATOR_CLIENT_CONFIG

    assert COORDINATOR_CLIENT_CONFIG.read_timeout <= 10
    assert COORDINATOR_CLIENT_CONFIG.connect_timeout <= 5
    assert COORDINATOR_CLIENT_CONFIG.tcp_keepalive is True
    assert COORDINATOR_CLIENT_CONFIG.retries["max_attempts"] >= 3
