"""A phone check-in over Twilio Media Streams: the same `VoiceCheckin`, a different line.

Twilio opens the WebSocket when the resident answers (`<Connect><Stream>` in the dialer's TwiML)
and sends `connected`, then `start` with the stream and call ids, the account, the media format
and our `<Parameter name="token">`. Stream URLs carry no query string (Twilio docs), which is
why the token travels as a parameter and is checked here, on `start`, before a model is opened.

Audio is 8 kHz mu-law both ways; Nova 2 Sonic is set to 8 kHz in and out, so the only conversion
is the codec. Barge-in sends Twilio `clear`, which drops audio it has buffered. The end of the
call sends a `mark` and waits for Twilio to echo it, so the closing words are heard before the
bridge closes the stream and the TwiML's `<Hangup/>` ends the call.

Run it with `make phone-bridge` (uvicorn + ngrok on this machine for the gate and the video).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, WebSocket

from .audio import PHONE_RATE, pcm16_to_ulaw, ulaw_to_pcm16
from .ports import PlaybackClock
from .serve import VoiceDeps
from .session import CallRecord, VoiceCheckin
from .sink import SetupError, load_setup
from .tokens import TokenError, verify

log = logging.getLogger(__name__)

PHONE_MAX_SECONDS = 240.0
START_TIMEOUT_SECONDS = 10.0


class TwilioPort:
    def __init__(self, websocket: Any, stream_sid: str) -> None:
        self.ws = websocket
        self.stream_sid = stream_sid
        self.playback = PlaybackClock(PHONE_RATE)
        self.open = True
        self._marks: dict[str, asyncio.Event] = {}
        self._mark_seq = 0
        self.frames_in = 0

    async def receive(self) -> bytes | None:
        while self.open:
            try:
                raw = await self.ws.receive_text()
            except Exception:  # noqa: BLE001 - Twilio closed the socket: the call is over
                self.open = False
                return None
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            event = message.get("event")
            if event == "media":
                payload = (message.get("media") or {}).get("payload") or ""
                self.frames_in += 1
                return ulaw_to_pcm16(base64.b64decode(payload))
            if event == "mark":
                name = (message.get("mark") or {}).get("name", "")
                if name in self._marks:
                    self._marks[name].set()
            elif event == "stop":
                self.open = False
                return None
        return None

    async def _send(self, message: dict[str, Any]) -> None:
        if not self.open:
            return
        try:
            await self.ws.send_text(json.dumps(message))
        except Exception:  # noqa: BLE001
            self.open = False

    async def play(self, pcm: bytes) -> None:
        self.playback.sent(len(pcm))
        payload = base64.b64encode(pcm16_to_ulaw(pcm)).decode()
        await self._send(
            {"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}}
        )

    async def clear(self) -> None:
        self.playback.cleared()
        await self._send({"event": "clear", "streamSid": self.stream_sid})

    async def drained(self, timeout: float) -> None:
        """Twilio echoes a mark once everything before it has played."""
        self._mark_seq += 1
        name = f"drain-{self._mark_seq}"
        done = self._marks[name] = asyncio.Event()
        await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}})
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except TimeoutError:
            await self.playback.drained(0.1)

    async def notify(self, event: dict[str, Any]) -> None:
        return None

    async def close(self, reason: str) -> None:
        if not self.open:
            return
        self.open = False
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass


async def read_start(websocket: Any) -> dict[str, Any] | None:
    """Wait for Twilio's `start` message (after `connected`)."""

    async def loop() -> dict[str, Any] | None:
        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("event") == "start":
                return message.get("start") or {}
            if message.get("event") == "stop":
                return None

    try:
        return await asyncio.wait_for(loop(), START_TIMEOUT_SECONDS)
    except (TimeoutError, ValueError):
        return None
    except Exception:  # noqa: BLE001 - the socket closed first
        return None


async def serve_phone(websocket: Any, deps: VoiceDeps, *, subaccount_sid: str) -> CallRecord | None:
    """Run one phone check-in on an accepted Twilio Media Streams WebSocket."""
    start = await read_start(websocket)
    if start is None:
        log.warning("phone stream closed before start")
        return None
    port = TwilioPort(websocket, str(start.get("streamSid") or ""))

    async def refuse(reason: str) -> None:
        log.warning("phone call refused: %s (call %s)", reason, str(start.get("callSid"))[:10])
        await port.close(reason)

    fmt = start.get("mediaFormat") or {}
    if fmt.get("encoding") != "audio/x-mulaw" or int(fmt.get("sampleRate") or 0) != PHONE_RATE:
        await refuse(f"unexpected media format {fmt}")
        return None
    if not subaccount_sid or start.get("accountSid") != subaccount_sid:
        await refuse("stream is not from the doorstep subaccount")
        return None
    if deps.flags.kill_switch():
        await refuse("kill switch is on")
        return None
    token = str((start.get("customParameters") or {}).get("token") or "")
    try:
        claims = verify(token, deps.secret, channel="phone")
    except TokenError as exc:
        await refuse(f"token: {exc}")
        return None
    if claims["mode"] != "live":
        await refuse("phone tokens are for live incidents only")
        return None
    if not deps.backend.claim(f"VOICE#{claims['jti']}"):
        await refuse("token already used")
        return None
    try:
        setup = load_setup(
            deps.backend,
            claims,
            channel="phone",
            input_rate=PHONE_RATE,
            output_rate=PHONE_RATE,
            max_seconds=PHONE_MAX_SECONDS,
        )
    except SetupError as exc:
        await refuse(str(exc))
        return None
    kwargs = {"agent_factory": deps.agent_factory} if deps.agent_factory else {}
    session = VoiceCheckin(setup, deps.sink, **kwargs)
    log.info("phone call started: incident=%s resident=%s", claims["inc"], claims["res"])
    record = await session.run(port)
    log.info(
        "phone call ended: resident=%s reason=%s turns=%d urgent=%s page_delivered=%s usage=%s",
        claims["res"],
        record.end_reason,
        len(record.transcript),
        record.urgent_source or "no",
        record.page_delivered,
        record.usage,
    )
    return record


def build_app(deps_factory: Any) -> FastAPI:
    app = FastAPI(title="Doorstep phone bridge")
    state: dict[str, VoiceDeps] = {}

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.websocket("/twilio")
    async def twilio_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        if "deps" not in state:
            state["deps"] = deps_factory()
        await serve_phone(
            websocket, state["deps"], subaccount_sid=os.environ.get("TWILIO_SUBACCOUNT_SID", "")
        )

    return app
