"""Voice session tokens: short-lived, single-use, HMAC-SHA256 signed. Standard library only.

A token is what lets one browser tab or one phone call become one check-in for one resident. It
names the incident, the resident, the channel and the mode, so a voice process takes all of that
from the signature and nothing from what the caller sends. Single use is enforced by the voice
process claiming `jti` in DynamoDB before it opens a model stream; this module only signs and
checks.

The signing key is derived from the internal HMAC secret with a fixed label, so a token can never
be confused with any other value signed by the same secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

VERSION = 1
LABEL = b"doorstep-voice-token-v1"
MAX_TTL_SECONDS = 300
CHANNELS = ("browser", "phone")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


class TokenError(ValueError):
    """The token is malformed, forged, expired or for something else. The message is safe."""


def _key(secret: str) -> bytes:
    if not secret:
        raise TokenError("no signing secret configured")
    return hmac.new(secret.encode(), LABEL, hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    secret: str,
    *,
    incident_id: str,
    resident_id: str,
    channel: str,
    mode: str,
    ttl_seconds: int = 60,
    now: float | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return `(token, claims)`."""
    if channel not in CHANNELS:
        raise TokenError(f"unknown channel {channel!r}")
    if not (_ID.match(incident_id) and _ID.match(resident_id)):
        raise TokenError("malformed incident or resident id")
    if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise TokenError("ttl out of range")
    issued = int(now if now is not None else time.time())
    claims: dict[str, Any] = {
        "v": VERSION,
        "jti": secrets.token_hex(12),
        "inc": incident_id,
        "res": resident_id,
        "ch": channel,
        "mode": mode,
        "iat": issued,
        "exp": issued + ttl_seconds,
        **(extra or {}),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", claims


def verify(
    token: str, secret: str, *, channel: str | None = None, now: float | None = None
) -> dict[str, Any]:
    """The claims of a valid token, or `TokenError`. Checks signature first, then time."""
    if not token or len(token) > 2048 or token.count(".") != 1:
        raise TokenError("malformed token")
    body, sig = token.split(".")
    expected = _b64(hmac.new(_key(secret), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise TokenError("bad signature")
    try:
        claims = json.loads(_unb64(body))
    except ValueError as exc:
        raise TokenError("malformed token") from exc
    if not isinstance(claims, dict) or claims.get("v") != VERSION:
        raise TokenError("unsupported token version")
    t = now if now is not None else time.time()
    if not isinstance(claims.get("exp"), int) or t >= claims["exp"]:
        raise TokenError("token expired")
    if claims.get("iat", 0) > t + 30:
        raise TokenError("token issued in the future")
    if channel is not None and claims.get("ch") != channel:
        raise TokenError("token is for another channel")
    for field in ("jti", "inc", "res", "mode"):
        if not isinstance(claims.get(field), str) or not claims[field]:
            raise TokenError(f"token has no {field}")
    return claims
