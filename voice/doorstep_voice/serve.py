"""Admitting a browser call: every check happens before a model stream is opened.

Order, cheapest and most decisive first: kill switch, token signature and expiry, channel and
mode, single use (a conditional write on `jti`), then the incident and case. A caller who fails
any of these costs a WebSocket accept and a DynamoDB read at most, never a Nova 2 Sonic session.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from doorstep_agent.store_dynamo import DynamoBackend

from .audio import BROWSER_RATE
from .ports import BrowserPort
from .session import CallRecord, ResultSink, VoiceCheckin
from .sink import SetupError, load_setup
from .tokens import TokenError, verify

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Voice-Token"
BROWSER_MAX_SECONDS = 180.0
BROWSER_MODES = frozenset({"drill", "sandbox"})


class Flags(Protocol):
    def kill_switch(self) -> bool: ...


@dataclass
class VoiceDeps:
    backend: DynamoBackend
    sink: ResultSink
    flags: Flags
    secret_env: str = "INTERNAL_HMAC_SECRET"
    agent_factory: Any = None

    @property
    def secret(self) -> str:
        return os.environ.get(self.secret_env, "")


def token_from(headers: dict[str, str] | None, query: dict[str, str] | None = None) -> str:
    for source in (headers or {}, query or {}):
        for name, value in source.items():
            if name.lower() == TOKEN_HEADER.lower():
                return value
    return ""


async def serve_browser(websocket: Any, token: str, deps: VoiceDeps) -> CallRecord | None:
    """Run one browser check-in on an accepted WebSocket. Returns the record, or None if refused."""
    port = BrowserPort(websocket, BROWSER_RATE)
    if deps.flags.kill_switch():
        await port.error("Voice check-ins are paused right now. Please try again later.", 4503)
        return None
    try:
        claims = verify(token, deps.secret, channel="browser")
    except TokenError as exc:
        log.info("browser call refused: %s", exc)
        await port.error(f"This call link is not valid ({exc}). Start a new call.", 4401)
        return None
    if claims["mode"] not in BROWSER_MODES:
        await port.error("Browser check-ins run in drills and the sandbox only.", 4403)
        return None
    if not deps.backend.claim(f"VOICE#{claims['jti']}"):
        log.info("browser call refused: token %s… reused", claims["jti"][:6])
        await port.error("This call link was already used. Start a new call.", 4409)
        return None
    try:
        setup = load_setup(
            deps.backend,
            claims,
            channel="browser",
            input_rate=BROWSER_RATE,
            output_rate=BROWSER_RATE,
            max_seconds=BROWSER_MAX_SECONDS,
        )
    except SetupError as exc:
        await port.error(f"This check-in cannot start: {exc}.", 4404)
        return None
    kwargs = {"agent_factory": deps.agent_factory} if deps.agent_factory else {}
    session = VoiceCheckin(setup, deps.sink, **kwargs)
    log.info("browser call started: incident=%s resident=%s", claims["inc"], claims["res"])
    record = await session.run(port)
    log.info(
        "browser call ended: resident=%s reason=%s turns=%d urgent=%s usage=%s",
        claims["res"],
        record.end_reason,
        len(record.transcript),
        record.urgent_source or "no",
        record.usage,
    )
    return record
