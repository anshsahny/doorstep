"""Dashboard access tokens: who may read an incident and answer its decisions. Stdlib only.

Two scopes:

* `sandbox` — issued by `POST /drills` with the visitor's own drill. It names that one incident,
  and a request for any other incident is refused before anything is read.
* `captain` — issued by `POST /captain/session` for the right passcode. Any incident; decisions
  are still checked against the roster by the coordinator, so it answers as the captain only.

The signing key is derived from the internal HMAC secret with its own label, like voice tokens,
so neither kind of token can ever pass as the other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from .common import INCIDENT_ID, header

VERSION = 1
LABEL = b"doorstep-dashboard-access-v1"
SCOPES = ("sandbox", "captain")
SANDBOX_TTL_SECONDS = 60 * 60
CAPTAIN_TTL_SECONDS = 12 * 60 * 60


class AccessError(ValueError):
    """Missing, forged, expired, or for another incident. The message is safe to return."""


def _key(secret: str) -> bytes:
    if not secret:
        raise AccessError("access is not configured")
    return hmac.new(secret.encode(), LABEL, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint(
    secret: str, *, scope: str, incident_id: str = "", ttl_seconds: int, now: float | None = None
) -> str:
    if scope not in SCOPES:
        raise AccessError(f"unknown scope {scope!r}")
    if scope == "sandbox" and not INCIDENT_ID.match(incident_id):
        raise AccessError("a sandbox token names one incident")
    issued = int(now if now is not None else time.time())
    claims = {"v": VERSION, "scope": scope, "inc": incident_id, "iat": issued}
    claims["exp"] = issued + ttl_seconds
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(token: str, secret: str, *, now: float | None = None) -> dict[str, Any]:
    if not token or len(token) > 1024 or token.count(".") != 1:
        raise AccessError("sign in or start a drill first")
    body, sig = token.split(".")
    expected = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise AccessError("that session is not valid; start a new drill")
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError as exc:
        raise AccessError("that session is not valid; start a new drill") from exc
    if not isinstance(claims, dict) or claims.get("v") != VERSION:
        raise AccessError("that session is not valid; start a new drill")
    if claims.get("scope") not in SCOPES:
        raise AccessError("that session is not valid; start a new drill")
    t = now if now is not None else time.time()
    if not isinstance(claims.get("exp"), int) or t >= claims["exp"]:
        raise AccessError("that session has expired; start a new drill")
    return claims


def for_incident(event: dict[str, Any], secret: str | None, incident_id: str) -> dict[str, Any]:
    """The verified claims of the request's bearer token, if they cover `incident_id`."""
    raw = header(event, "authorization")
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
    claims = verify(token, secret or "")
    if claims["scope"] == "sandbox" and claims.get("inc") != incident_id:
        raise AccessError("that drill belongs to another session")
    return claims


def subject(claims: dict[str, Any]) -> str:
    """The identity the coordinator resolves (see `doorstep_agent.decisions._resolve_web`)."""
    return "captain" if claims["scope"] == "captain" else f"sandbox:{claims['inc']}"
