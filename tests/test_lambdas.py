"""The Lambda handlers: every webhook is safe to receive twice, and every paid path is capped."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from doorstep_agent.cloud.coordinator import runtime_session_id as agent_session_id
from doorstep_agent.store_dynamo import create_table
from doorstep_api import admin_replay, alert_poller, telegram_webhook
from doorstep_api.common import Deps, runtime_session_id

SECRET = "webhook-secret-for-tests_0123456789"
PASSCODE = "passcode-for-tests"


class FakeAgentCore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False
        self.reply: dict[str, Any] = {"ok": True, "accepted": True}

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("runtime unavailable")
        self.calls.append({**kwargs, "payload": json.loads(kwargs["payload"])})
        return {"response": io.BytesIO(json.dumps(self.reply).encode())}


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch) -> Iterator[Deps]:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ORG_LAT", "45.4855")
    monkeypatch.setenv("ORG_LNG", "-122.5945")
    with mock_aws():
        ssm = boto3.client("ssm")
        for name, value in {
            "telegram/webhook_secret": SECRET,
            "captain_passcode": PASSCODE,
            "kill_switch": "off",
            "nws_user_agent": "(doorstep-test, test@example.com)",
            "caps": json.dumps({"per_ip_per_10min": 2, "daily": 3, "total": 4}),
        }.items():
            ssm.put_parameter(Name=f"/doorstep/{name}", Value=value, Type="SecureString")
        dynamodb = boto3.client("dynamodb")
        create_table(dynamodb, "doorstep")
        yield Deps(
            table="doorstep",
            runtime_arn="arn:test",
            _ssm=ssm,
            _dynamodb=dynamodb,
            _agentcore=FakeAgentCore(),
        )


def webhook_event(update: dict[str, Any], secret: str = SECRET) -> dict[str, Any]:
    return {"headers": {"x-telegram-bot-api-secret-token": secret}, "body": json.dumps(update)}


def tap_update(update_id: int, data: str = "d|drill-20260912-190501-a1b2|dec-001|handle") -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cb",
            "from": {"id": 42, "first_name": "Not forwarded"},
            "message": {"message_id": 9, "chat": {"id": 42}, "text": "not forwarded either"},
            "data": data,
        },
    }


# --- Telegram webhook ----------------------------------------------------------------------


def test_a_wrong_secret_touches_nothing(deps: Deps) -> None:
    result = telegram_webhook.handler(webhook_event(tap_update(1), secret="nope"), deps=deps)
    assert result["statusCode"] == 401
    assert deps._agentcore.calls == []
    assert deps.dynamodb.scan(TableName="doorstep")["Count"] == 0


def test_a_tap_is_forwarded_once_however_often_telegram_sends_it(deps: Deps) -> None:
    for _ in range(3):
        assert (
            telegram_webhook.handler(webhook_event(tap_update(7)), deps=deps)["statusCode"] == 200
        )
    assert len(deps._agentcore.calls) == 1
    call = deps._agentcore.calls[0]
    assert call["runtimeSessionId"] == runtime_session_id("drill-20260912-190501-a1b2")
    forwarded = call["payload"]["event"]
    assert forwarded["type"] == "decision_response"
    assert forwarded["callback_query"]["message"]["chat"]["id"] == 42
    assert "text" not in forwarded["callback_query"]["message"], "forward only what is needed"


def test_a_failed_forward_releases_the_claim_so_telegrams_retry_lands(deps: Deps) -> None:
    deps._agentcore.fail = True
    assert telegram_webhook.handler(webhook_event(tap_update(8)), deps=deps)["statusCode"] == 502
    deps._agentcore.fail = False
    assert telegram_webhook.handler(webhook_event(tap_update(8)), deps=deps)["statusCode"] == 200
    assert telegram_webhook.handler(webhook_event(tap_update(8)), deps=deps)["statusCode"] == 200
    assert len(deps._agentcore.calls) == 1


def test_updates_that_are_not_doorstep_buttons_are_acknowledged_and_dropped(deps: Deps) -> None:
    for update in (
        {"update_id": 20, "message": {"text": "/start"}},
        tap_update(21, data="d|dec-001|handle"),
        tap_update(22, data="d|../../etc|dec-001|handle"),
        {"no": "update id"},
    ):
        assert telegram_webhook.handler(webhook_event(update), deps=deps)["statusCode"] == 200
    assert deps._agentcore.calls == []


def test_the_kill_switch_stops_forwarding(deps: Deps) -> None:
    deps.ssm.put_parameter(Name="/doorstep/kill_switch", Value="on", Type="String", Overwrite=True)
    assert telegram_webhook.handler(webhook_event(tap_update(30)), deps=deps)["statusCode"] == 200
    assert deps._agentcore.calls == []


def test_both_session_id_functions_agree() -> None:
    for incident in ("inc-test", "drill-20260912-190501-a1b2", "alert-20260912-190501-ab12"):
        assert runtime_session_id(incident) == agent_session_id(incident)


# --- admin replay --------------------------------------------------------------------------


def replay_event(
    key: str = "key-00000001", passcode: str = PASSCODE, ip: str = "1.2.3.4", **body: Any
) -> dict:
    return {
        "headers": {"x-doorstep-passcode": passcode, "idempotency-key": key},
        "requestContext": {"http": {"sourceIp": ip}},
        "body": json.dumps(body),
    }


def test_a_replay_starts_one_drill_and_a_retry_returns_the_same_incident(deps: Deps) -> None:
    first = admin_replay.handler(replay_event(telegram=True), deps=deps)
    again = admin_replay.handler(replay_event(telegram=True), deps=deps)
    assert first["statusCode"] == 202 and again["statusCode"] == 200
    assert json.loads(again["body"]) == {
        "incident_id": json.loads(first["body"])["incident_id"],
        "replayed": True,
    }
    assert len(deps._agentcore.calls) == 1
    event = deps._agentcore.calls[0]["payload"]["event"]
    assert event == {
        "type": "replay",
        "telegram": True,
        "auto_approve": False,
        "decision_ttl_minutes": 15.0,
        "timeout_seconds": 240.0,
        "voice_residents": [],
    }


def test_a_wrong_passcode_is_refused_and_eventually_rate_limited(deps: Deps) -> None:
    statuses = [
        admin_replay.handler(replay_event(passcode="guess"), deps=deps)["statusCode"]
        for _ in range(12)
    ]
    assert statuses[:10] == [401] * 10 and statuses[10:] == [429, 429]
    locked = admin_replay.handler(replay_event(key="key-right-guess"), deps=deps)
    assert locked["statusCode"] == 429, "locked out means the right passcode too"
    assert deps._agentcore.calls == []


def test_per_ip_daily_and_total_caps_hold(deps: Deps) -> None:
    codes = [
        admin_replay.handler(replay_event(key=f"key-ip-{i:04d}"), deps=deps)["statusCode"]
        for i in range(3)
    ]
    assert codes == [202, 202, 429], "two per IP per ten minutes"
    codes = [
        admin_replay.handler(replay_event(key=f"key-other-{i:04d}", ip=f"9.9.9.{i}"), deps=deps)[
            "statusCode"
        ]
        for i in range(3)
    ]
    assert codes == [202, 429, 429], "daily cap of 3 reached"
    assert len(deps._agentcore.calls) == 3


def test_a_refused_replay_can_be_retried_with_the_same_key(deps: Deps) -> None:
    deps._agentcore.fail = True
    assert admin_replay.handler(replay_event(key="key-retry-001"), deps=deps)["statusCode"] == 502
    deps._agentcore.fail = False
    assert admin_replay.handler(replay_event(key="key-retry-001"), deps=deps)["statusCode"] == 202


def test_replay_needs_an_idempotency_key_and_honours_the_kill_switch(deps: Deps) -> None:
    assert admin_replay.handler(replay_event(key="x"), deps=deps)["statusCode"] == 400
    deps.ssm.put_parameter(Name="/doorstep/kill_switch", Value="on", Type="String", Overwrite=True)
    deps._params.clear()
    assert admin_replay.handler(replay_event(), deps=deps)["statusCode"] == 503
    assert deps._agentcore.calls == []


# --- alert poller --------------------------------------------------------------------------


def nws(*events: str) -> Any:
    def fetch(user_agent: str, lat: str, lng: str) -> list[dict[str, Any]]:
        assert user_agent.startswith("(doorstep-test") and lat == "45.4855"
        return [
            {"properties": {"id": f"urn:oid:{e}", "event": e, "severity": "Severe"}} for e in events
        ]

    return fetch


def test_the_poller_records_each_alert_once_and_starts_nothing_in_observe_mode(deps: Deps) -> None:
    fetch = nws("Excessive Heat Warning", "Air Quality Alert")
    assert alert_poller.handler(deps=deps, fetch=fetch) == {
        "alerts": 2,
        "new": 2,
        "forwarded": 0,
        "mode": "observe",
    }
    assert alert_poller.handler(deps=deps, fetch=fetch)["new"] == 0
    assert deps._agentcore.calls == []


def test_the_poller_forwards_new_alerts_in_drill_mode(deps: Deps) -> None:
    deps.ssm.put_parameter(Name="/doorstep/poller_mode", Value="drill", Type="String")
    summary = alert_poller.handler(deps=deps, fetch=nws("Extreme Heat Warning"))
    assert summary["forwarded"] == 1
    assert deps._agentcore.calls[0]["payload"]["event"]["type"] == "alert"
