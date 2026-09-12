"""POST /telegram/webhook: Telegram button taps, delivered at least once, applied at most once.

1. The secret token header must match (constant time) or nothing else is read.
2. The `update_id` is claimed in DynamoDB before anything happens. Telegram re-sends an update
   until it gets a 2xx, so a repeat finds the claim and is acknowledged without effect.
3. The tap is forwarded to the incident's coordinator, which checks identity and answers the
   decision once. If forwarding fails, the claim is released and a 5xx lets Telegram retry.

This function holds no bot token and reads no incident data.
"""

from __future__ import annotations

from typing import Any

from . import common
from .common import DEPS, INCIDENT_ID, body_of, header, log, response, same_secret


def handler(event: dict[str, Any], context: Any = None, deps: common.Deps | None = None) -> dict:
    deps = deps or DEPS
    given = header(event, "x-telegram-bot-api-secret-token")
    if not same_secret(given, deps.param("telegram/webhook_secret")):
        log(msg="webhook refused", reason="secret token mismatch")
        return response(401, {"ok": False})

    update = body_of(event)
    if update is None or not isinstance(update.get("update_id"), int):
        return response(200, {"ok": True, "ignored": "not an update"})
    update_id = update["update_id"]
    key = f"TGU#{update_id}"
    if not deps.claim(key):
        log(msg="duplicate update ignored", update_id=update_id)
        return response(200, {"ok": True, "duplicate": True})

    query = update.get("callback_query")
    parts = str((query or {}).get("data") or "").split("|")
    if not isinstance(query, dict) or len(parts) != 4 or parts[0] != "d":
        return response(200, {"ok": True, "ignored": "not a Doorstep button"})
    incident_id = parts[1]
    if not INCIDENT_ID.match(incident_id):
        return response(200, {"ok": True, "ignored": "malformed incident"})
    if deps.kill_switch():
        log(msg="tap dropped: kill switch on", update_id=update_id, incident=incident_id)
        return response(200, {"ok": True, "paused": True})

    forwarded = {
        "type": "decision_response",
        "update_id": update_id,
        "callback_query": {
            "id": query.get("id"),
            "data": query.get("data"),
            "from": {"id": (query.get("from") or {}).get("id")},
            "message": {
                "message_id": (query.get("message") or {}).get("message_id"),
                "chat": {"id": ((query.get("message") or {}).get("chat") or {}).get("id")},
            },
        },
    }
    try:
        result = deps.invoke(incident_id, forwarded)
    except Exception as exc:  # noqa: BLE001 - release so Telegram's retry can succeed
        deps.release(key)
        log(msg="forward failed; claim released", update_id=update_id, error=type(exc).__name__)
        return response(502, {"ok": False})
    log(
        msg="tap forwarded",
        update_id=update_id,
        incident=incident_id,
        accepted=result.get("accepted"),
    )
    return response(200, {"ok": True})
