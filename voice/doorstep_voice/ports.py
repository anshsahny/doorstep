"""The browser's side of a call, over a Starlette WebSocket (AgentCore Runtime `/ws`, or local).

Wire protocol, kept deliberately small so Phase 5's dashboard can reuse the test page's client:

    browser -> voice   binary: PCM16 little-endian mono at 16 kHz, any chunk size up to 64 KB
                       text:   {"type": "hangup"}
    voice -> browser   binary: PCM16 little-endian mono at 16 kHz (the agent speaking)
                       text:   {"type": "clear"}                      stop playback now (barge-in)
                               {"type": "transcript", "speaker", "text"}
                               {"type": "status", "status"}
                               {"type": "ended", "reason"}
                               {"type": "error", "message"}

Anything else a browser sends is ignored. The browser never names a resident, an incident or a
result: those come from the token, and the result from the coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .audio import seconds_of_pcm16

log = logging.getLogger(__name__)


class PlaybackClock:
    """When the far end will have finished hearing what was sent, assuming real-time playback."""

    def __init__(self, rate: int, clock: Any = time.monotonic) -> None:
        self.rate = rate
        self.clock = clock
        self.until = 0.0

    def sent(self, pcm_bytes: int) -> None:
        self.until = max(self.until, self.clock()) + seconds_of_pcm16(pcm_bytes, self.rate)

    def cleared(self) -> None:
        self.until = self.clock()

    async def drained(self, timeout: float, *, grace: float = 0.4) -> None:
        deadline = self.clock() + timeout
        while self.clock() < min(self.until + grace, deadline):
            await asyncio.sleep(0.05)


class BrowserPort:
    def __init__(self, websocket: Any, rate: int) -> None:
        self.ws = websocket
        self.playback = PlaybackClock(rate)
        self.open = True
        self.bytes_in = 0
        self.bytes_out = 0

    async def receive(self) -> bytes | None:
        while self.open:
            message = await self.ws.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                self.open = False
                return None
            if message.get("bytes"):
                data: bytes = message["bytes"]
                if len(data) % 2:
                    data = data[:-1]
                self.bytes_in += len(data)
                return data
            text = message.get("text")
            if text:
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "hangup":
                    return None
        return None

    async def _send(self, **kwargs: Any) -> None:
        if not self.open:
            return
        try:
            if "data" in kwargs:
                await self.ws.send_bytes(kwargs["data"])
            else:
                await self.ws.send_text(json.dumps(kwargs["event"]))
        except Exception:  # noqa: BLE001 - the caller went away mid-send
            self.open = False

    async def play(self, pcm: bytes) -> None:
        self.playback.sent(len(pcm))
        self.bytes_out += len(pcm)
        await self._send(data=pcm)

    async def clear(self) -> None:
        self.playback.cleared()
        await self._send(event={"type": "clear"})

    async def drained(self, timeout: float) -> None:
        await self.playback.drained(timeout)

    async def notify(self, event: dict[str, Any]) -> None:
        await self._send(event=event)

    async def error(self, message: str, code: int = 4400) -> None:
        await self._send(event={"type": "error", "message": message})
        await self._close(code)

    async def close(self, reason: str) -> None:
        await self._send(event={"type": "ended", "reason": reason})
        await self._close(1000)

    async def _close(self, code: int) -> None:
        if not self.open:
            return
        self.open = False
        try:
            await self.ws.close(code=code)
        except Exception:  # noqa: BLE001
            pass
