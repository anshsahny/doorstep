"""POST /voice/session: a 60-second link for one browser check-in (Nova 2 Sonic costs money).

The browser asks to answer the call for one resident of one incident. In order, cheapest first:
kill switch; the request shape; a dashboard token for this incident (a visitor's own sandbox drill,
or the captain's); the incident must be a drill or sandbox incident and the resident
one it reserved for voice (so nobody can talk over a simulated or real check-in); a per-IP limit;
a daily cap; a cap for the whole judging period. Only then is a token minted and a SigV4 presigned
WebSocket URL to the voice runtime returned, with the token signed into it.

The voice runtime checks the token again, claims it once, and holds each call to three minutes.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

from doorstep_voice.tokens import mint

from . import access, common
from .common import DEPS, body_of, log, response

RESIDENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Voice-Token"
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
URL_TTL_SECONDS = 60
DEFAULT_VOICE_CAPS = {"voice_per_ip_per_hour": 4, "voice_daily": 20, "voice_total": 150}


def voice_caps(deps: common.Deps) -> dict[str, int]:
    try:
        configured = json.loads(deps.param("caps") or "{}")
    except ValueError:
        configured = {}
    return {k: int(configured.get(k, v)) for k, v in DEFAULT_VOICE_CAPS.items()}


def presigned_ws_url(
    runtime_arn: str, session_id: str, token: str, *, credentials: Any, region: str
) -> str:
    """The same URL `AgentCoreRuntimeClient.generate_presigned_url` builds, without the SDK."""
    host = f"bedrock-agentcore.{region}.amazonaws.com"
    query = urlencode({TOKEN_HEADER: token, SESSION_HEADER: session_id})
    url = f"https://{host}/runtimes/{quote(runtime_arn, safe='')}/ws?{query}"
    request = AWSRequest(method="GET", url=url, headers={"host": urlparse(url).hostname})
    SigV4QueryAuth(credentials, "bedrock-agentcore", region, expires=URL_TTL_SECONDS).add_auth(
        request
    )
    return request.url.replace("https://", "wss://", 1)


def incident_doc(deps: common.Deps, incident_id: str) -> dict[str, Any] | None:
    item = deps.dynamodb.get_item(
        TableName=deps.table,
        Key={"PK": {"S": f"INC#{incident_id}"}, "SK": {"S": "META"}},
        ConsistentRead=True,
    ).get("Item")
    if not item or "doc" not in item:
        return None
    return json.loads(item["doc"]["S"])


def handler(event: dict[str, Any], context: Any = None, deps: common.Deps | None = None) -> dict:
    deps = deps or DEPS
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "unknown"))
    now = datetime.now(UTC)

    if deps.kill_switch():
        return response(503, {"error": "Voice check-ins are paused right now."})
    body = body_of(event)
    if body is None:
        return response(400, {"error": "The body must be a JSON object."})
    incident_id = str(body.get("incident_id") or "")
    resident_id = str(body.get("resident_id") or "")
    if not common.INCIDENT_ID.match(incident_id) or not RESIDENT_ID.match(resident_id):
        return response(400, {"error": "Send incident_id and resident_id."})
    try:
        access.for_incident(event, deps.param("internal_hmac_secret"), incident_id)
    except access.AccessError as exc:
        return response(401, {"error": f"Voice check-ins need your drill session: {exc}."})

    doc = incident_doc(deps, incident_id)
    if doc is None:
        return response(404, {"error": "No such drill."})
    mode = str(doc.get("mode") or "")
    reserved = (doc.get("run_options") or {}).get("voice_residents") or []
    if mode not in ("drill", "sandbox"):
        return response(403, {"error": "Browser check-ins run in drills and the sandbox only."})
    if resident_id not in reserved:
        return response(403, {"error": "That resident is not waiting for a voice call."})

    limits = voice_caps(deps)
    checks = (
        ("per_ip", f"RATE#voice#{ip}#{now:%Y%m%d%H}", limits["voice_per_ip_per_hour"], 7200,
         "Please wait a while before starting another call."),
        ("daily", f"CAP#voice#{now:%Y%m%d}", limits["voice_daily"], 3 * 24 * 3600,
         "Today's voice call limit is used up. Please try tomorrow."),
        ("total", "CAP#voice#total", limits["voice_total"], 400 * 24 * 3600,
         "The voice call limit for the judging period is used up."),
    )  # fmt: skip
    for name, counter, limit, ttl, message in checks:
        if not deps.count(counter, limit=limit, ttl_seconds=ttl):
            log(msg="voice session refused", reason=name)
            return response(429, {"error": message})

    secret = deps.param("internal_hmac_secret")
    runtime_arn = deps.voice_runtime_arn
    if not secret or not runtime_arn:
        log(msg="voice session misconfigured", secret=bool(secret), runtime=bool(runtime_arn))
        return response(500, {"error": "Voice is not configured."})
    token, claims = mint(
        secret,
        incident_id=incident_id,
        resident_id=resident_id,
        channel="browser",
        mode=mode,
        ttl_seconds=URL_TTL_SECONDS,
    )
    session = deps.boto_session
    url = presigned_ws_url(
        runtime_arn,
        f"doorstep-voice-{claims['jti']}",
        token,
        credentials=session.get_credentials().get_frozen_credentials(),
        region=session.region_name or "us-east-1",
    )
    log(msg="voice session issued", incident=incident_id, resident=resident_id)
    return response(
        201,
        {"url": url, "expires_in": URL_TTL_SECONDS, "max_call_seconds": 180, "sample_rate": 16000},
    )
