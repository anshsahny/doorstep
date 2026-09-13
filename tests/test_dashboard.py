"""The dashboard API (Gate 5): sandbox isolation, every cost cap, and what a visitor can read.

All offline on moto. The deployed versions of the cap checks are `make cap-test`.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from doorstep_agent.store_dynamo import create_table
from doorstep_api import access, dashboard, snapshot
from doorstep_api.common import Deps

SECRET = "internal-hmac-secret-for-tests-0123456789"
PASSCODE = "passcode-for-tests"
INC = "sandbox-20260913-120000-abc123"


class FakeAgentCore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False
        self.reply: dict[str, Any] = {"ok": True, "accepted": True}

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("runtime unavailable")
        self.calls.append(json.loads(kwargs["payload"]))
        return {"response": io.BytesIO(json.dumps(self.reply).encode())}


CAPS = {
    "sandbox_per_ip_per_10min": 1,
    "sandbox_per_10min": 3,
    "sandbox_daily": 4,
    "sandbox_total": 5,
    "passcode_failures_per_hour": 2,
}


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch) -> Iterator[Deps]:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    dashboard._ROSTER.clear()
    with mock_aws():
        ssm = boto3.client("ssm")
        for name, value in {
            "captain_passcode": PASSCODE,
            "internal_hmac_secret": SECRET,
            "kill_switch": "off",
            "caps": json.dumps(CAPS),
            "demo_video_url": "https://example.com/video",
        }.items():
            ssm.put_parameter(Name=f"/doorstep/{name}", Value=value, Type="SecureString")
        dynamodb = boto3.client("dynamodb")
        create_table(dynamodb, "doorstep")
        yield Deps(table="doorstep", runtime_arn="arn:test", _ssm=ssm, _dynamodb=dynamodb,
                   _agentcore=FakeAgentCore())  # fmt: skip
    dashboard._ROSTER.clear()


def kill(deps: Deps, value: str = "on") -> None:
    deps.ssm.put_parameter(Name="/doorstep/kill_switch", Value=value, Type="String", Overwrite=True)
    deps._params.clear()


def start(ip: str = "1.1.1.1", key: str = "") -> dict[str, Any]:
    headers = {"idempotency-key": key} if key else {}
    return dashboard.handler(
        {
            "routeKey": "POST /drills",
            "requestContext": {"http": {"sourceIp": ip}},
            "headers": headers,
        },
        deps=DEPS_HOLDER[0],
    )


DEPS_HOLDER: list[Deps] = []


@pytest.fixture(autouse=True)
def hold(deps: Deps) -> Iterator[None]:
    DEPS_HOLDER[:] = [deps]
    yield
    DEPS_HOLDER.clear()


def counters(deps: Deps) -> dict[str, int]:
    items = deps.dynamodb.scan(TableName="doorstep")["Items"]
    return {i["PK"]["S"]: int(i["n"]["N"]) for i in items if i["SK"]["S"] == "COUNT"}


def body(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["body"])


def token(scope: str = "sandbox", incident: str = INC) -> str:
    return access.mint(
        SECRET, scope=scope, incident_id=incident if scope == "sandbox" else "", ttl_seconds=600
    )


# --- POST /drills: isolation and caps -------------------------------------------------------


def test_a_drill_is_a_sandbox_with_no_channel_options_and_a_token_for_that_drill_only(
    deps: Deps,
) -> None:
    result = start()
    assert result["statusCode"] == 202
    started = body(result)
    assert started["incident_id"].startswith("sandbox-")
    assert deps._agentcore.calls == [
        {
            "incident_id": started["incident_id"],
            "event": {"type": "sandbox", "voice_residents": ["r06"]},
        }
    ]
    claims = access.verify(started["token"], SECRET)
    assert claims["scope"] == "sandbox" and claims["inc"] == started["incident_id"]


def test_one_drill_per_ip_per_ten_minutes(deps: Deps) -> None:
    assert start("2.2.2.2")["statusCode"] == 202
    refused = start("2.2.2.2")
    assert refused["statusCode"] == 429
    assert body(refused)["recorded_drill"] is True
    assert body(refused)["video_url"] == "https://example.com/video"
    assert len(deps._agentcore.calls) == 1


def test_everyone_together_is_capped_per_ten_minutes_per_day_and_in_total(deps: Deps) -> None:
    statuses = [start(f"10.0.0.{i}")["statusCode"] for i in range(5)]
    assert statuses == [202, 202, 202, 429, 429], "3 drills per 10 minutes for everyone"
    assert len(deps._agentcore.calls) == 3

    # Move to a fresh 10-minute window by clearing that counter: the daily cap (4) binds next.
    for key in list(counters(deps)):
        if key.startswith("CAP#sandbox#window#"):
            deps.dynamodb.delete_item(
                TableName="doorstep", Key={"PK": {"S": key}, "SK": {"S": "COUNT"}}
            )
    assert [start(f"11.0.0.{i}")["statusCode"] for i in range(2)] == [202, 429]
    assert len(deps._agentcore.calls) == 4


def test_the_total_cap_holds_even_on_a_new_day(deps: Deps) -> None:
    deps.dynamodb.put_item(
        TableName="doorstep",
        Item={"PK": {"S": "CAP#sandbox#total"}, "SK": {"S": "COUNT"}, "n": {"N": "5"}},
    )
    refused = start("3.3.3.3")
    assert refused["statusCode"] == 429 and "judging period" in body(refused)["error"]
    assert deps._agentcore.calls == []


def test_a_refused_request_gives_back_the_counts_it_took(deps: Deps) -> None:
    """A busy window must not cost a judge their own per-IP turn, or burn the day's cap."""
    deps.dynamodb.put_item(
        TableName="doorstep",
        Item={"PK": {"S": "CAP#sandbox#total"}, "SK": {"S": "COUNT"}, "n": {"N": "5"}},
    )
    assert start("4.4.4.4")["statusCode"] == 429
    left = {k: v for k, v in counters(deps).items() if k != "CAP#sandbox#total"}
    assert all(v == 0 for v in left.values()), left


def test_the_kill_switch_refuses_before_anything_is_counted(deps: Deps) -> None:
    kill(deps)
    result = start("5.5.5.5")
    assert result["statusCode"] == 503 and body(result)["recorded_drill"] is True
    assert counters(deps) == {} and deps._agentcore.calls == []


def test_a_kill_switch_that_cannot_be_read_fails_closed(deps: Deps) -> None:
    deps.ssm.delete_parameter(Name="/doorstep/kill_switch")
    deps._params.clear()
    assert start("5.5.5.6")["statusCode"] == 503


def test_a_retried_click_returns_the_same_drill(deps: Deps) -> None:
    first = body(start("6.6.6.6", key="click-0001"))
    again = start("6.6.6.6", key="click-0001")
    assert again["statusCode"] == 200
    assert body(again)["incident_id"] == first["incident_id"] and body(again)["replayed"]
    assert len(deps._agentcore.calls) == 1


def test_a_drill_that_did_not_start_costs_the_visitor_nothing(deps: Deps) -> None:
    deps._agentcore.fail = True
    assert start("7.7.7.7")["statusCode"] == 502
    assert all(v == 0 for v in counters(deps).values())
    deps._agentcore.fail = False
    assert start("7.7.7.7")["statusCode"] == 202


# --- captain sessions ----------------------------------------------------------------------


def captain(passcode: str, ip: str = "8.8.8.8") -> dict[str, Any]:
    return dashboard.handler(
        {
            "routeKey": "POST /captain/session",
            "requestContext": {"http": {"sourceIp": ip}},
            "body": json.dumps({"passcode": passcode}),
        },
        deps=DEPS_HOLDER[0],
    )


def test_the_captain_passcode_is_locked_out_after_repeated_failures(deps: Deps) -> None:
    assert [captain("wrong")["statusCode"] for _ in range(3)] == [401, 401, 429]
    assert captain(PASSCODE)["statusCode"] == 429, "locked out means the right guess too"
    good = captain(PASSCODE, ip="8.8.4.4")
    assert good["statusCode"] == 201
    assert access.verify(body(good)["token"], SECRET)["scope"] == "captain"


# --- reading an incident --------------------------------------------------------------------


def put(deps: Deps, pk: str, sk: str, doc: dict[str, Any]) -> None:
    deps.dynamodb.put_item(
        TableName="doorstep",
        Item={"PK": {"S": pk}, "SK": {"S": sk}, "doc": {"S": json.dumps(doc)}},
    )


def seed(deps: Deps, incident: str = INC, mode: str = "sandbox") -> None:
    put(deps, "ORG#juniper-court", "PROFILE", {"id": "juniper-court", "captain_id": "cap-maria",
        "phone_tree_baseline": {"minutes_per_call": 4, "volunteers_calling": 1}})  # fmt: skip
    put(deps, "ORG#juniper-court", "RES#r01", {
        "id": "r01", "first_name": "Rose", "name": "Rose Whitaker", "unit": "3C",
        "building": "Juniper Court", "phone_ref": "env:CALL_ALLOWLIST[0]",
        "family_contact_ref": "contact:r01-daughter", "consent": {"share_with_volunteer": True},
        "notes": ["Hard of hearing: speak slowly."], "age_band": "80+",
    })  # fmt: skip
    put(deps, "ORG#juniper-court", "VOL#cap-maria", {"id": "cap-maria", "name": "Maria Delgado",
        "role": "captain", "telegram_chat_id_ref": "env:TELEGRAM_CAPTAIN_CHAT_ID"})  # fmt: skip
    put(deps, f"INC#{incident}", "META", {"id": incident, "mode": mode, "status": "active",
        "started_at": "2026-09-13T12:00:00Z", "resident_ids": ["r01"],
        "alert": {"event": "Excessive Heat Warning"},
        "run_options": {"voice_residents": ["r06"]}})  # fmt: skip
    put(deps, f"INC#{incident}", "CASE#r01", {
        "resident_id": "r01", "state": "ESCALATED", "attempts": 1,
        "risk": {"points": 10, "wave": 1, "factors": ["age_80_plus"]},
        "results": [{"status": "URGENT", "key_quote": "call me on +1 503 555 0100",
                     "flagged_mid_call": True}],
        "attempt_log": [{"attempt": 1, "channel": "simulated", "started_at": "2026-09-13T12:00:05Z",
                         "transcript": [{"speaker": "resident", "text": "I'm dizzy"}]}],
        "history": [
            {"from_state": "QUEUED", "to_state": "CALLING", "at": "2026-09-13T12:00:05Z"},
            {"from_state": "CALLING", "to_state": "URGENT", "at": "2026-09-13T12:00:20Z"},
        ],
    })  # fmt: skip
    put(deps, f"INC#{incident}", "DEC#dec-001", {
        "id": "dec-001", "resident_id": "r01", "name": "doorstep-urgent-red-flag",
        "reason": "dizzy", "status": "pending", "audience": "cap-maria",
        "created_at": "2026-09-13T12:00:21Z",
        "options": [{"id": "handle", "label": "I'm handling it", "action": "resolve",
                     "args": {"secret_arg": "x"}}],
    })  # fmt: skip
    for seq in (1, 2, 3):
        put(deps, f"INC#{incident}", f"EVT#{seq:08d}", {"seq": seq, "actor": "agent:x",
            "type": "tool_call", "reason": f"event {seq}",
            "data": {"session": {"a": 1}}})  # fmt: skip
    put(deps, f"INC#{incident}", "EVT#00000004", {"seq": 4, "actor": "agent:dispatcher",
        "type": "policy", "policy_decision": "deny", "tool": "notify_family",
        "reason": "no consent"})  # fmt: skip


def read(incident: str, bearer: str, **query: str) -> dict[str, Any]:
    return dashboard.handler(
        {
            "routeKey": "GET /incidents/{incident_id}",
            "pathParameters": {"incident_id": incident},
            "headers": {"authorization": f"Bearer {bearer}"},
            "queryStringParameters": query or None,
        },
        deps=DEPS_HOLDER[0],
    )


def test_a_visitor_reads_only_their_own_drill(deps: Deps) -> None:
    seed(deps)
    seed(deps, incident="sandbox-someone-else-1")
    assert read(INC, token())["statusCode"] == 200
    assert read("sandbox-someone-else-1", token())["statusCode"] == 401
    assert read(INC, "")["statusCode"] == 401
    assert read(INC, token()[:-3] + "xyz")["statusCode"] == 401
    expired = access.mint(SECRET, scope="sandbox", incident_id=INC, ttl_seconds=1, now=1_000)
    assert read(INC, expired)["statusCode"] == 401
    assert read("sandbox-someone-else-1", token("captain"))["statusCode"] == 200


def test_the_board_carries_no_contact_references_and_masks_numbers(deps: Deps) -> None:
    seed(deps)
    text = read(INC, token())["body"]
    leaks = ("phone_ref", "CALL_ALLOWLIST", "family_contact_ref", "r01-daughter",
             "telegram_chat_id_ref", "TELEGRAM_", "555 0100", "secret_arg", "Whitaker")  # fmt: skip
    for leak in leaks:
        assert leak not in text, leak
    board = json.loads(text)
    assert board["residents"][0]["first_name"] == "Rose" and board["residents"][0]["notes"]
    assert "[number]" in board["cases"][0]["result"]["key_quote"]
    assert board["incident"]["started_at"] == "2026-09-13T12:00:00Z"
    assert board["fictional"] is True


def test_polling_returns_only_new_audit_rows(deps: Deps) -> None:
    seed(deps)
    first = body(read(INC, token()))
    assert [e["seq"] for e in first["events"]] == [1, 2, 3, 4] and first["last_seq"] == 4
    later = body(read(INC, token(), since="2"))
    assert [e["seq"] for e in later["events"]] == [3, 4]
    assert "residents" not in later, "the roster is sent once, not on every poll"


def test_the_report_counts_people_automation_denials_and_the_baseline(deps: Deps) -> None:
    seed(deps)
    report = body(read(INC, token(), view="report"))
    assert report["residents"] == 1 and report["reached"] == 1 and report["all_reached"]
    assert report["seconds_to_first_call"] == 5.0 and report["seconds_to_reach_everyone"] == 20.0
    assert report["outcomes"] == {"URGENT": 1}
    assert report["urgent"][0]["call_to_page_seconds"] == 16.0
    assert report["decisions"]["waiting"] == 1
    assert [d["reason"] for d in report["policy_denials"]] == ["no consent"]
    assert report["phone_tree"]["minutes"] == 4.0


# --- answering a decision --------------------------------------------------------------------


def answer(incident: str, decision: str, option: str, bearer: str) -> dict[str, Any]:
    return dashboard.handler(
        {
            "routeKey": "POST /incidents/{incident_id}/decisions/{decision_id}",
            "pathParameters": {"incident_id": incident, "decision_id": decision},
            "headers": {"authorization": f"Bearer {bearer}"},
            "body": json.dumps({"option_id": option}),
        },
        deps=DEPS_HOLDER[0],
    )


def test_an_answer_becomes_the_same_decision_response_a_telegram_tap_does(deps: Deps) -> None:
    seed(deps)
    assert answer(INC, "dec-001", "handle", token())["statusCode"] == 202
    assert deps._agentcore.calls == [
        {
            "incident_id": INC,
            "event": {
                "type": "decision_response",
                "web": {
                    "decision_id": "dec-001",
                    "option_id": "handle",
                    "subject": f"sandbox:{INC}",
                },
            },
        }
    ]
    assert answer(INC, "dec-001", "handle", token("captain"))["statusCode"] == 202
    assert deps._agentcore.calls[-1]["event"]["web"]["subject"] == "captain"


def test_answers_that_cannot_apply_never_wake_the_coordinator(deps: Deps) -> None:
    seed(deps)
    seed(deps, incident="sandbox-someone-else-1")
    assert answer("sandbox-someone-else-1", "dec-001", "handle", token())["statusCode"] == 401
    assert answer(INC, "dec-001", "not_an_option", token())["statusCode"] == 400
    assert answer(INC, "dec-999", "handle", token())["statusCode"] == 404
    assert answer(INC, "../x", "handle", token())["statusCode"] == 400
    kill(deps)
    assert answer(INC, "dec-001", "handle", token())["statusCode"] == 503
    kill(deps, "off")
    put(deps, f"INC#{INC}", "DEC#dec-001", {"id": "dec-001", "status": "answered", "options": []})
    stale = answer(INC, "dec-001", "handle", token())
    assert stale["statusCode"] == 409 and body(stale)["decision"]["status"] == "answered"
    assert deps._agentcore.calls == []


def test_mask_catches_numbers_wherever_they_turn_up() -> None:
    masked = snapshot.mask({"a": ["call +15035550100 now"], "b": {"c": "(503) 555-0100"}})
    assert masked == {"a": ["call [number] now"], "b": {"c": "[number]"}}
    masked = snapshot.mask({"d": "text me at 503-555-0100 or 5035550100, +1 (503) 555 0100"})
    assert masked == {"d": "text me at [number] or [number], [number]"}
    kept = {
        "t": "3 of 12 at 12:00:05",
        "at": "2026-09-13T12:00:21Z",
        "u": "2026-09-12 23:41:30",
        "id": "sandbox-20260913-144916-8ad64a",
        "drill": "drill-20260912-234122-d132",
    }
    assert snapshot.mask(kept) == kept
