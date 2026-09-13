"""One voice check-in: a Strands Agents `BidiAgent` on Nova 2 Sonic running the SPEC §9 protocol.

The script, questions, red flags and tools are the text check-in's own
(`checkin_text.protocol_prompt(channel="voice")`, `CHECKIN_TOOLS`), so a hazard profile edit
changes what the voice agent asks exactly as it changes the text agent.

What only a live call needs is here:

* **Mid-call escalation.** The captain is paged the moment a red flag is known, not at hang-up,
  by whichever comes first: the agent calling `flag_urgent`, or the deterministic phrase backstop
  matching the resident's own words as each transcript arrives. Strands runs a bidi tool call as
  its own task while audio keeps flowing (Phase 4 spike S1), so the page never stalls the call.
  Tool interrupts do not exist in bidi; the page is an event to the coordinator, which escalates.
* **A call that ends after the page is out.** Once a red flag has fired, the session does not hang
  up until the coordinator has put the decision in front of the captain, or `page_wait_seconds`
  have passed (audited either way).
* **Barge-in.** Sonic's own voice-activity detection interrupts its reply; the port is told to
  drop whatever audio it still has queued.
* **Hard limits.** A time cap per call, a silence cap, and one model stream per token.

The model proposes nothing final here. The raw attempt (Sonic's transcript, the recorded
answers, the flags) goes to the coordinator, which classifies it with the same model, protocol
check, mid-call flag rule and phrase backstop as a text check-in.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from doorstep_agent.agents.checkin_text import CALL_CONNECTED, protocol_prompt
from doorstep_agent.backstop import find_red_flags
from doorstep_agent.models import OrgProfile, Resident
from doorstep_agent.profiles import HazardProfile
from doorstep_agent.tools import CHECKIN_TOOLS

log = logging.getLogger(__name__)

# Nova 2 Sonic voices by resident language (Nova 2 Sonic language support: tiffany en-US,
# lupe es-US).
VOICES = {"en": "tiffany", "es": "lupe"}
SONIC_MODEL_ID = "amazon.nova-2-sonic-v1:0"


class AudioPort(Protocol):
    """The caller's side of the line: a browser tab or a phone call."""

    async def receive(self) -> bytes | None:
        """The next chunk of caller audio as PCM16 at the input rate; None when they hung up."""
        ...

    async def play(self, pcm: bytes) -> None: ...

    async def clear(self) -> None:
        """Drop audio queued for playback (barge-in)."""
        ...

    async def drained(self, timeout: float) -> None:
        """Return once everything sent has been heard, or after `timeout` seconds."""
        ...

    async def notify(self, event: dict[str, Any]) -> None:
        """A small JSON event for the caller's screen (transcripts, status). Phones ignore it."""
        ...

    async def close(self, reason: str) -> None: ...


class ResultSink(Protocol):
    """Where a call's events go: the incident's coordinator."""

    async def urgent(self, claims: dict[str, Any], event: dict[str, Any]) -> None: ...
    async def attempt(self, claims: dict[str, Any], event: dict[str, Any]) -> None: ...
    async def page_delivered(self, claims: dict[str, Any]) -> bool: ...


@dataclass
class CallSetup:
    claims: dict[str, Any]
    resident: Resident
    profile: HazardProfile
    org: OrgProfile
    channel: str  # "browser" | "phone"
    input_rate: int
    output_rate: int
    max_seconds: float = 180.0
    silence_seconds: float = 25.0
    page_wait_seconds: float = 20.0
    drain_seconds: float = 10.0
    closing_warning_seconds: float = 25.0

    @property
    def voice(self) -> str:
        return VOICES.get(self.resident.language, VOICES["en"])


@dataclass
class CallRecord:
    """What happened on the call, in the shape the coordinator's `checkin_attempt` event takes."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    end_reason: str = ""
    urgent_sent_at: datetime | None = None
    urgent_source: str = ""
    page_delivered: bool | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    interruptions: int = 0

    def add(self, speaker: str, text: str) -> str:
        """Append transcript text, merging a speaker's consecutive pieces. Returns the turn."""
        text = " ".join(text.split())
        if not text:
            return ""
        if self.transcript and self.transcript[-1]["speaker"] == speaker:
            self.transcript[-1]["text"] = f"{self.transcript[-1]['text']} {text}"
        else:
            self.transcript.append({"speaker": speaker, "text": text})
        return self.transcript[-1]["text"]


def default_agent_factory(setup: CallSetup, prompt: str, boto_session: Any = None) -> Any:
    from strands.experimental.bidi import BidiAgent
    from strands.experimental.bidi.models import BedrockNovaSonicModel

    kwargs: dict[str, Any] = {"boto_session": boto_session} if boto_session else {}
    model = BedrockNovaSonicModel(
        model_id=SONIC_MODEL_ID,
        audio={
            "input_rate": setup.input_rate,
            "output_rate": setup.output_rate,
            "channels": 1,
            "format": "pcm",
            "voice": setup.voice,
        },
        params={"turnDetectionConfiguration": {"endpointingSensitivity": "MEDIUM"}},
        **kwargs,
    )
    return BidiAgent(model=model, system_prompt=prompt, tools=CHECKIN_TOOLS)


class _UsageTap(logging.Handler):
    """Nova reports speech and text tokens separately, but Strands only logs that at DEBUG."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.by_prompt: dict[str, dict[str, Any]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "nova usage event" not in msg or "usage_details=<" not in msg:
            return
        try:
            raw = json.loads(msg.split("usage_details=<", 1)[1].rsplit(">", 1)[0])
        except ValueError:
            return
        if isinstance(raw, dict) and raw.get("promptName"):
            self.by_prompt[raw["promptName"]] = raw


USAGE_TAP = _UsageTap()
_provider_log = logging.getLogger("strands.experimental.bidi.models.bedrock")
_provider_log.addHandler(USAGE_TAP)
_provider_log.setLevel(logging.DEBUG)
_provider_log.propagate = False  # the tap reads DEBUG; nothing else should see audio-sized logs


class VoiceCheckin:
    def __init__(
        self,
        setup: CallSetup,
        sink: ResultSink,
        *,
        agent_factory: Callable[[CallSetup, str], Any] = default_agent_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.setup = setup
        self.sink = sink
        self.agent_factory = agent_factory
        self.clock = clock
        self.record = CallRecord()
        self.call: dict[str, Any] = {"answers": {}, "urgent": [], "ended": False, "summary": ""}
        self._urgent_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_activity = 0.0
        self._t0 = 0.0
        self._agent: Any = None
        self._warned = False

    # --- the page ---

    def _on_urgent_from_tool(self, reason: str) -> None:
        """Called by `flag_urgent` inside a tool thread; schedules the page and returns."""
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self.page_captain, reason, "agent")

    def page_captain(self, reason: str, source: str) -> None:
        """Send the urgent event once per call. Later flags ride along in the final attempt."""
        if self._urgent_task is not None:
            return
        self.record.urgent_sent_at = datetime.now(UTC)
        self.record.urgent_source = source
        claims = self.setup.claims
        event = {
            "resident_id": claims["res"],
            "attempt_key": claims["jti"],
            "channel": self.setup.channel,
            "reason": reason[:300],
            "source": source,
            "at": self.record.urgent_sent_at.isoformat(),
            "started_at": self.record.started_at.isoformat(),
            "resident_words": [
                t["text"] for t in self.record.transcript if t["speaker"] == "resident"
            ][-3:],
        }
        log.info("urgent page for %s (source=%s)", claims["res"], source)
        self._urgent_task = asyncio.get_running_loop().create_task(self._send_urgent(event))

    async def _send_urgent(self, event: dict[str, Any]) -> None:
        try:
            await self.sink.urgent(self.setup.claims, event)
        except Exception:  # noqa: BLE001 - the final attempt still carries the flag
            log.exception("urgent page failed; the final attempt will still escalate")

    # --- the call ---

    async def run(self, port: AudioPort) -> CallRecord:
        self._loop = asyncio.get_running_loop()
        self._t0 = self._last_activity = self.clock()
        prompt = protocol_prompt(
            self.setup.profile, self.setup.org, self.setup.resident, channel="voice"
        )
        agent = self._agent = self.agent_factory(self.setup, prompt)
        state = {
            "call": self.call,
            "on_urgent": self._on_urgent_from_tool,
            "resident_id": self.setup.resident.id,
            "actor": "agent:checkin_voice",
        }
        await agent.start(invocation_state=state)
        tasks: dict[str, asyncio.Task[Any]] = {}
        try:
            await agent.send(CALL_CONNECTED)
            await port.notify({"type": "status", "status": "connected"})
            tasks = {
                "caller": asyncio.create_task(self._pump_caller(port, agent)),
                "model": asyncio.create_task(self._pump_model(port, agent)),
                "watch": asyncio.create_task(self._watch(port, agent)),
            }
            done, _ = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
            finished = next(name for name, t in tasks.items() if t in done)
            if not self.record.end_reason:
                self.record.end_reason = {
                    "caller": "caller_hung_up",
                    "model": "model_closed",
                    "watch": "completed",
                }[finished]
            for t in done:
                if t.exception() is not None:
                    log.error("call task %s failed: %r", finished, t.exception())
                    self.record.end_reason = f"error:{type(t.exception()).__name__}"
        finally:
            for t in tasks.values():
                t.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            await self._stop_agent(agent)
            self.record.ended_at = datetime.now(UTC)
            await port.close(self.record.end_reason or "ended")
            if self._urgent_task is not None:
                await asyncio.gather(self._urgent_task, return_exceptions=True)
        await self.sink.attempt(self.setup.claims, self.attempt_event())
        return self.record

    async def _stop_agent(self, agent: Any) -> None:
        connection = getattr(getattr(agent, "model", None), "_connection_id", None)
        try:
            await asyncio.wait_for(agent.stop(), 10)
        except Exception as exc:  # noqa: BLE001 - the record is what matters now
            log.warning("agent stop: %s", type(exc).__name__)
        raw = USAGE_TAP.by_prompt.pop(connection, None) if connection else None
        if raw:
            self.record.usage.update({"nova": raw.get("details", {}).get("total", {})})

    async def _pump_caller(self, port: AudioPort, agent: Any) -> None:
        from strands.experimental.bidi.types.events import BidiAudioInputEvent

        while True:
            pcm = await port.receive()
            if pcm is None:
                return
            await agent.send(
                BidiAudioInputEvent(
                    audio=base64.b64encode(pcm).decode(),
                    format="pcm",
                    sample_rate=self.setup.input_rate,  # type: ignore[arg-type]
                    channels=1,
                )
            )

    async def _pump_model(self, port: AudioPort, agent: Any) -> None:
        from strands.experimental.bidi.types.events import (
            BidiAudioStreamEvent,
            BidiConnectionCloseEvent,
            BidiErrorEvent,
            BidiInterruptionEvent,
            BidiTranscriptStreamEvent,
            BidiUsageEvent,
        )

        async for event in agent.receive():
            if isinstance(event, BidiAudioStreamEvent):
                self._last_activity = self.clock()
                await port.play(base64.b64decode(event.audio))
            elif isinstance(event, BidiInterruptionEvent):
                self.record.interruptions += 1
                await port.clear()
            elif isinstance(event, BidiTranscriptStreamEvent):
                if event.is_final:
                    await self._transcript(port, agent, event.role, event.text or "")
            elif isinstance(event, BidiUsageEvent):
                self.record.usage.update(
                    {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens}
                )
            elif isinstance(event, BidiErrorEvent):
                self.record.end_reason = f"model_error:{type(event.error).__name__}"
                return
            elif isinstance(event, BidiConnectionCloseEvent):
                return

    async def _transcript(self, port: AudioPort, agent: Any, role: str, text: str) -> None:
        speaker = "resident" if role == "user" else "agent"
        turn = self.record.add(speaker, text)
        if not turn:
            return
        self._last_activity = self.clock()
        await port.notify({"type": "transcript", "speaker": speaker, "text": text.strip()})
        if speaker != "resident":
            return
        # The deterministic backstop, live: the resident's own words, the whole current turn
        # (Sonic splits one utterance across transcripts), the same profile phrases as the text
        # path. It can only add urgency.
        matches = find_red_flags(turn, self.setup.profile)
        if matches and not self.call["urgent"]:
            phrases = sorted({m.phrase for m in matches})
            self.page_captain(f"resident said: {', '.join(phrases)}", "backstop")
            # No text is sent to the agent here: on call 3 a bracketed nudge after Sonic had
            # already said the red-flag line made it answer "sorry, this call cannot proceed".
            # The page does not depend on the agent; its own prompt handles red flags.

    async def _watch(self, port: AudioPort, agent: Any) -> None:
        s = self.setup
        while True:
            await asyncio.sleep(0.2)
            now = self.clock()
            elapsed = now - self._t0
            if self.call["ended"]:
                await port.drained(s.drain_seconds)
                if self._urgent_task is not None:
                    self.record.page_delivered = await self._wait_for_page()
                self.record.end_reason = "completed"
                return
            if elapsed >= s.max_seconds:
                self.record.end_reason = "time_limit"
                if self._urgent_task is not None:
                    self.record.page_delivered = await self._wait_for_page()
                return
            if not self._warned and elapsed >= s.max_seconds - s.closing_warning_seconds:
                self._warned = True
                await agent.send(
                    "[Time is nearly up. Close the call now: say the tip and the closing line, "
                    "then call end_call.]"
                )
            if now - self._last_activity >= s.silence_seconds:
                self.record.end_reason = "silence"
                return

    async def _wait_for_page(self) -> bool:
        """Hold the line until the captain's decision is out, or the wait runs out."""
        deadline = self.clock() + self.setup.page_wait_seconds
        if self._urgent_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._urgent_task), self.setup.page_wait_seconds
                )
            except TimeoutError:
                return False
        while self.clock() < deadline:
            try:
                if await self.sink.page_delivered(self.setup.claims):
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("page check failed: %s", type(exc).__name__)
            await asyncio.sleep(0.5)
        return False

    def attempt_event(self) -> dict[str, Any]:
        r = self.record
        claims = self.setup.claims
        return {
            "resident_id": claims["res"],
            "attempt_key": claims["jti"],
            "channel": self.setup.channel,
            "answered": True,
            "started_at": r.started_at.isoformat(),
            "ended_at": (r.ended_at or datetime.now(UTC)).isoformat(),
            "transcript": r.transcript,
            "answers": dict(self.call["answers"]),
            "urgent_flags": list(self.call["urgent"]),
            "summary": str(self.call.get("summary") or ""),
            "end_reason": r.end_reason,
            "urgent_sent_at": r.urgent_sent_at.isoformat() if r.urgent_sent_at else None,
            "urgent_source": r.urgent_source,
            "page_delivered": r.page_delivered,
            "interruptions": r.interruptions,
            "usage": r.usage,
        }
