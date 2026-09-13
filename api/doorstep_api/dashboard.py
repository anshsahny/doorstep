"""The dashboard API (SPEC §11, §14): public sandbox drills, captain sessions, reads and answers.

Routes:

* `POST /drills` — a visitor's own 12-resident sandbox drill. It spends money (about $0.38), so in
  order: kill switch; an optional `Idempotency-Key` so a retried click returns the same drill;
  then four counters, narrowest first: per IP per 10 minutes, everyone per 10 minutes, per day,
  and for the whole judging period. A request one counter refuses is taken back off the counters
  before it, so a busy moment never costs a judge their own per-IP turn.
* `POST /captain/session` — the captain passcode (constant time, 10 failures an hour per IP)
  for a captain token.
* `GET /incidents/{id}` — the board (`?since=<seq>` for new audit rows only) or `?view=report`.
* `POST /incidents/{id}/decisions/{decision_id}` — one answer, forwarded to the incident's
  coordinator as the same `decision_response` event a Telegram tap becomes.

Nothing here can reach a real channel: a sandbox start event carries no channel options at all,
and the coordinator ignores any it is sent.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import UTC, datetime
from typing import Any

from . import access, common, snapshot
from .common import DEPS, INCIDENT_ID, body_of, header, log, response, same_secret

DECISION_ID = re.compile(r"^dec-[0-9]{3,6}$")
OPTION_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
VOICE_RESIDENT = os.getenv("DOORSTEP_SANDBOX_VOICE_RESIDENT", "r06")
DEFAULT_SANDBOX_CAPS = {
    "sandbox_per_ip_per_10min": 1,
    "sandbox_per_10min": 3,
    "sandbox_daily": 15,
    "sandbox_total": 120,
    "passcode_failures_per_hour": 10,
}
_ROSTER: dict[str, Any] = {}
_ROSTER_LOCK = threading.Lock()


def sandbox_caps(deps: common.Deps) -> dict[str, int]:
    try:
        configured = json.loads(deps.param("caps") or "{}")
    except ValueError:
        configured = {}
    return {k: int(configured.get(k, v)) for k, v in DEFAULT_SANDBOX_CAPS.items()}


def limited(message: str, deps: common.Deps) -> dict[str, Any]:
    """Every cap says what happened and offers what still works."""
    return response(
        429,
        {
            "error": message,
            "recorded_drill": True,
            "video_url": deps.param("demo_video_url") or "",
        },
    )


def source_ip(event: dict[str, Any]) -> str:
    return str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "unknown"))


def handler(event: dict[str, Any], context: Any = None, deps: common.Deps | None = None) -> dict:
    deps = deps or DEPS
    route = str(event.get("routeKey") or "")
    params = event.get("pathParameters") or {}
    try:
        if route == "POST /drills":
            return start_drill(event, deps)
        if route == "POST /captain/session":
            return captain_session(event, deps)
        if route == "GET /incidents/{incident_id}":
            return read_incident(event, deps, str(params.get("incident_id") or ""))
        if route == "POST /incidents/{incident_id}/decisions/{decision_id}":
            return answer(
                event,
                deps,
                str(params.get("incident_id") or ""),
                str(params.get("decision_id") or ""),
            )
    except access.AccessError as exc:
        return response(401, {"error": str(exc)[:1].upper() + str(exc)[1:] + "."})
    return response(404, {"error": "Not found."})


# --- POST /drills -----------------------------------------------------------------------------


def start_drill(event: dict[str, Any], deps: common.Deps) -> dict[str, Any]:
    if deps.kill_switch():
        return response(
            503,
            {
                "error": "Doorstep's sandbox is paused right now. You can still watch a recorded "
                "drill.",
                "recorded_drill": True,
                "video_url": deps.param("demo_video_url") or "",
            },
        )
    secret = deps.param("internal_hmac_secret")
    if not secret:
        log(msg="drill refused", reason="no signing secret")
        return response(500, {"error": "The sandbox is not configured."})

    now = datetime.now(UTC)
    ip = source_ip(event)
    incident_id = f"sandbox-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    key = header(event, "idempotency-key")
    idem = f"IDEM#drills#{key}" if IDEMPOTENCY_KEY.match(key) else ""
    if idem and not deps.claim(idem, incident_id=incident_id):
        earlier = deps.claimed(idem).get("incident_id", "")
        if not INCIDENT_ID.match(earlier):
            return response(409, {"error": "That drill is still starting. Try again."})
        return response(200, drill_body(earlier, secret, replayed=True))

    limits = sandbox_caps(deps)
    window = f"{now:%Y%m%d%H}{now.minute // 10}"
    checks = (
        (f"RATE#sandbox#{ip}#{window}", limits["sandbox_per_ip_per_10min"], 3600,
         "You started a drill a moment ago. Please wait 10 minutes, or keep watching that one."),
        (f"CAP#sandbox#window#{window}", limits["sandbox_per_10min"], 3600,
         "A few drills are running right now. Please try again in a few minutes."),
        (f"CAP#sandbox#{now:%Y%m%d}", limits["sandbox_daily"], 3 * 24 * 3600,
         "Today's sandbox drills are used up. Watch a recorded drill, or try again tomorrow."),
        ("CAP#sandbox#total", limits["sandbox_total"], 400 * 24 * 3600,
         "The sandbox has reached its limit for the judging period. Watch a recorded drill."),
    )  # fmt: skip
    counted: list[str] = []
    for counter, limit, ttl, message in checks:
        if not deps.count(counter, limit=limit, ttl_seconds=ttl):
            for earlier in counted:
                deps.uncount(earlier)
            if idem:
                deps.release(idem)
            log(msg="drill refused", reason=counter.split("#")[1:3])
            return limited(message, deps)
        counted.append(counter)

    try:
        result = deps.invoke(incident_id, {"type": "sandbox", "voice_residents": [VOICE_RESIDENT]})
    except Exception as exc:  # noqa: BLE001 - nothing ran, so the visitor keeps their turn
        for counter in counted:
            deps.uncount(counter)
        if idem:
            deps.release(idem)
        log(msg="drill forward failed", error=type(exc).__name__)
        return response(502, {"error": "The drill did not start. Please try again."})
    if not result.get("accepted"):
        if idem:
            deps.release(idem)
        log(msg="drill not accepted", reason=str(result.get("error") or result.get("reason")))
        return response(503, {"error": "The drill did not start. Please try again shortly."})
    log(msg="sandbox drill started", incident=incident_id)
    return response(202, drill_body(incident_id, secret))


def drill_body(incident_id: str, secret: str, *, replayed: bool = False) -> dict[str, Any]:
    token = access.mint(
        secret,
        scope="sandbox",
        incident_id=incident_id,
        ttl_seconds=access.SANDBOX_TTL_SECONDS,
    )
    return {
        "incident_id": incident_id,
        "token": token,
        "expires_in": access.SANDBOX_TTL_SECONDS,
        "voice_resident": VOICE_RESIDENT,
        "replayed": replayed,
    }


# --- POST /captain/session ------------------------------------------------------------------


def captain_session(event: dict[str, Any], deps: common.Deps) -> dict[str, Any]:
    now = datetime.now(UTC)
    ip = source_ip(event)
    body = body_of(event) or {}
    limits = sandbox_caps(deps)
    failures = f"RATE#passfail#{ip}#{now:%Y%m%d%H}"
    # Checked before the passcode, so a locked-out address learns nothing, right guess included.
    if deps.reached(failures, limits["passcode_failures_per_hour"]):
        log(msg="captain session refused", reason="lockout")
        return response(429, {"error": "Too many tries. Please wait an hour."})
    if not same_secret(str(body.get("passcode") or ""), deps.param("captain_passcode")):
        counted = deps.count(
            failures,
            limit=limits["passcode_failures_per_hour"],
            ttl_seconds=7200,
        )
        log(msg="captain session refused", limited=not counted)
        if not counted:
            return response(429, {"error": "Too many tries. Please wait an hour."})
        return response(401, {"error": "That passcode is not right."})
    secret = deps.param("internal_hmac_secret")
    if not secret:
        return response(500, {"error": "Captain mode is not configured."})
    token = access.mint(secret, scope="captain", ttl_seconds=access.CAPTAIN_TTL_SECONDS)
    log(msg="captain session issued")
    return response(201, {"token": token, "expires_in": access.CAPTAIN_TTL_SECONDS})


# --- GET /incidents/{id} ----------------------------------------------------------------------


def read_incident(event: dict[str, Any], deps: common.Deps, incident_id: str) -> dict[str, Any]:
    if not INCIDENT_ID.match(incident_id):
        return response(400, {"error": "That is not a drill id."})
    access.for_incident(event, deps.param("internal_hmac_secret"), incident_id)
    query = event.get("queryStringParameters") or {}
    meta = item(deps, f"INC#{incident_id}", "META")
    if meta is None:
        return response(404, {"error": "That drill is still starting, or it does not exist."})
    pk = f"INC#{incident_id}"
    cases = rows(deps, pk, "CASE#")
    decisions = rows(deps, pk, "DEC#")
    now = datetime.now(UTC).isoformat()

    if query.get("view") == "report":
        events = rows(deps, pk, "EVT#")
        messages = rows(deps, pk, "MSG#")
        org = roster(deps).get("org", {})
        body = snapshot.report(
            incident=meta,
            cases=cases,
            decisions=decisions,
            events=events,
            messages=messages,
            org=org,
        )
        return response(200, snapshot.mask(body | {"server_time": now}))

    try:
        since = max(int(query.get("since") or 0), 0)
    except ValueError:
        since = 0
    events = rows(deps, pk, "EVT#", after=f"EVT#{since:08d}")
    static = roster(deps) if since == 0 else {}
    body = snapshot.build(
        incident=meta,
        cases=cases,
        decisions=decisions,
        events=events,
        residents=static.get("residents") if since == 0 else None,
        volunteers=static.get("volunteers") if since == 0 else None,
        now=now,
    )
    return response(200, body)


def item(deps: common.Deps, pk: str, sk: str) -> dict[str, Any] | None:
    found = deps.dynamodb.get_item(
        TableName=deps.table, Key={"PK": {"S": pk}, "SK": {"S": sk}}
    ).get("Item")
    return json.loads(found["doc"]["S"]) if found and "doc" in found else None


def rows(deps: common.Deps, pk: str, prefix: str, *, after: str = "") -> list[dict[str, Any]]:
    """Eventually consistent reads: half the price, and the board polls again in two seconds."""
    if not prefix:
        condition = "PK = :pk"
        values = {":pk": {"S": pk}}
    elif after:
        condition = "PK = :pk AND SK BETWEEN :a AND :b"
        values = {":pk": {"S": pk}, ":a": {"S": after + "~"}, ":b": {"S": prefix + "~"}}
    else:
        condition = "PK = :pk AND begins_with(SK, :p)"
        values = {":pk": {"S": pk}, ":p": {"S": prefix}}
    kwargs: dict[str, Any] = {
        "TableName": deps.table,
        "KeyConditionExpression": condition,
        "ExpressionAttributeValues": values,
    }
    out: list[dict[str, Any]] = []
    while True:
        page = deps.dynamodb.query(**kwargs)
        out += [json.loads(i["doc"]["S"]) for i in page.get("Items", []) if "doc" in i]
        if "LastEvaluatedKey" not in page or len(out) >= snapshot.MAX_EVENTS * 2:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def roster(deps: common.Deps) -> dict[str, Any]:
    """The fictional org, residents and volunteers, read once per Lambda container."""
    with _ROSTER_LOCK:
        if not _ROSTER:
            org_id = os.getenv("DOORSTEP_ORG_ID", "juniper-court")
            docs = rows(deps, f"ORG#{org_id}", "")
            _ROSTER["org"] = next((d for d in docs if "captain_id" in d), {})
            _ROSTER["residents"] = [d for d in docs if "first_name" in d]
            _ROSTER["volunteers"] = [d for d in docs if "telegram_chat_id_ref" in d]
        return dict(_ROSTER)


# --- POST /incidents/{id}/decisions/{decision_id} ---------------------------------------------


def answer(
    event: dict[str, Any], deps: common.Deps, incident_id: str, decision_id: str
) -> dict[str, Any]:
    if not INCIDENT_ID.match(incident_id) or not DECISION_ID.match(decision_id):
        return response(400, {"error": "That is not a decision on a drill."})
    claims = access.for_incident(event, deps.param("internal_hmac_secret"), incident_id)
    if deps.kill_switch():
        return response(503, {"error": "Doorstep is paused right now. Nothing was sent."})
    body = body_of(event) or {}
    option_id = str(body.get("option_id") or "")
    if not OPTION_ID.match(option_id):
        return response(400, {"error": "Choose one of the options."})
    decision = item(deps, f"INC#{incident_id}", f"DEC#{decision_id}")
    if decision is None:
        return response(404, {"error": "That decision is not on this drill."})
    if decision.get("status") != "pending":
        # Cheap answers to a stale screen, without waking the coordinator. The coordinator
        # enforces answered-once on its own; this only saves an invocation.
        return response(
            409,
            {
                "error": "That decision was already answered."
                if decision.get("status") == "answered"
                else "That decision is no longer open.",
                "decision": snapshot.decision_view(decision),
            },
        )
    if option_id not in {o["id"] for o in decision.get("options") or []}:
        return response(400, {"error": "That option is not on offer."})
    forwarded = {
        "type": "decision_response",
        "web": {
            "decision_id": decision_id,
            "option_id": option_id,
            "subject": access.subject(claims),
        },
    }
    try:
        result = deps.invoke(incident_id, forwarded)
    except Exception as exc:  # noqa: BLE001
        log(msg="answer forward failed", error=type(exc).__name__)
        return response(502, {"error": "Your answer did not go through. Please try again."})
    if not result.get("accepted"):
        return response(503, {"error": "Your answer did not go through. Please try again."})
    log(msg="answer forwarded", incident=incident_id, decision=decision_id, scope=claims["scope"])
    return response(202, {"accepted": True, "decision_id": decision_id, "option_id": option_id})
