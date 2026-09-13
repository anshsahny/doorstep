"""POST /admin/replay: start a cloud drill on the archived 2021 alert (captain passcode).

Spends money (about $0.38 a drill), so in order: kill switch, a per-IP limit on wrong passcodes,
the passcode itself (constant time), an `Idempotency-Key` so a retried request returns the same
incident instead of starting a second drill, then a per-IP rate limit, a daily cap and a total
cap, each counted atomically in DynamoDB.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from . import common
from .common import DEPS, body_of, header, log, response, runtime_session_id, same_secret

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
VOICE_RESIDENT = re.compile(r"^r[0-9]{2}$")
DEFAULT_CAPS = {"per_ip_per_10min": 2, "daily": 10, "total": 60, "passcode_failures_per_hour": 10}


def caps(deps: common.Deps) -> dict[str, int]:
    try:
        configured = json.loads(deps.param("caps") or "{}")
    except ValueError:
        configured = {}
    return {k: int(configured.get(k, v)) for k, v in DEFAULT_CAPS.items()}


def handler(event: dict[str, Any], context: Any = None, deps: common.Deps | None = None) -> dict:
    deps = deps or DEPS
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "unknown"))
    now = datetime.now(UTC)
    limits = caps(deps)

    if deps.kill_switch():
        return response(503, {"error": "Doorstep is paused right now. Please try again later."})

    hour = now.strftime("%Y%m%d%H")
    failures = f"RATE#passfail#{ip}#{hour}"
    # Checked before the passcode, so a locked-out address learns nothing, right guess included.
    if deps.reached(failures, limits["passcode_failures_per_hour"]):
        log(msg="replay refused", reason="passcode lockout")
        return response(429, {"error": "Too many tries. Please wait an hour."})
    if not same_secret(header(event, "x-doorstep-passcode"), deps.param("captain_passcode")):
        counted = deps.count(failures, limit=limits["passcode_failures_per_hour"], ttl_seconds=7200)
        log(msg="replay refused", reason="passcode", limited=not counted)
        status = 401 if counted else 429
        return response(status, {"error": "That passcode is not right."})

    key = header(event, "idempotency-key")
    if not IDEMPOTENCY_KEY.match(key):
        return response(
            400, {"error": "Send an Idempotency-Key header (8-64 letters, digits, - or _)."}
        )
    body = body_of(event)
    if body is None:
        return response(400, {"error": "The body must be a JSON object."})
    try:
        options = {
            "telegram": bool(body.get("telegram", False)),
            "auto_approve": bool(body.get("auto_approve", False)),
            "decision_ttl_minutes": min(max(float(body.get("decision_ttl_minutes", 15)), 0.5), 30),
            "timeout_seconds": min(max(float(body.get("timeout_seconds", 240)), 60), 1800),
            "voice_residents": sorted({str(r) for r in body.get("voice_residents") or []}),
        }
        if len(options["voice_residents"]) > 12 or not all(
            VOICE_RESIDENT.match(r) for r in options["voice_residents"]
        ):
            raise ValueError("voice_residents")
    except (TypeError, ValueError):
        return response(
            400, {"error": "Options must be booleans, numbers and a list of resident ids."}
        )

    incident_id = f"drill-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    idem = f"IDEM#replay#{key}"
    if not deps.claim(idem, incident_id=incident_id):
        earlier = deps.claimed(idem).get("incident_id", "")
        return response(200, {"incident_id": earlier, "replayed": True})

    # Narrowest first: a request refused by one limit has already been counted by the limits
    # checked before it, so the per-IP window goes first and the period-long total goes last.
    checks = (
        ("per_ip", f"RATE#replay#{ip}#{now:%Y%m%d%H}{now.minute // 10}", limits["per_ip_per_10min"],
         3600, "Please wait a few minutes before starting another drill."),
        ("daily", f"CAP#replay#{now:%Y%m%d}", limits["daily"], 3 * 24 * 3600,
         "Today's drill limit is used up. Please try tomorrow."),
        ("total", "CAP#replay#total", limits["total"], 400 * 24 * 3600,
         "The drill limit for the judging period is used up."),
    )  # fmt: skip
    for name, counter, limit, ttl, message in checks:
        if not deps.count(counter, limit=limit, ttl_seconds=ttl):
            deps.release(idem)
            log(msg="replay refused", reason=name)
            return response(429, {"error": message})

    try:
        result = deps.invoke(incident_id, {"type": "replay", **options})
    except Exception as exc:  # noqa: BLE001
        deps.release(idem)
        log(msg="replay forward failed", error=type(exc).__name__)
        return response(502, {"error": "The coordinator did not accept the drill. Try again."})
    if not result.get("accepted"):
        deps.release(idem)
        return response(
            409, {"error": result.get("error") or result.get("reason") or "not accepted"}
        )
    log(msg="replay started", incident=incident_id, telegram=options["telegram"])
    return response(
        202, {"incident_id": incident_id, "session_id": runtime_session_id(incident_id), **options}
    )
