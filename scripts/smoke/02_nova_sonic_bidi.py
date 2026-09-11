"""Smoke test 02: a Strands BidiAgent on Nova 2 Sonic holds a two-turn spoken exchange.

Default (speech mode): each user turn is synthesized with macOS `say`, converted to 16 kHz PCM16
with `afconvert`, and streamed to the model as if it were microphone audio. The model's spoken
replies are counted and its transcripts printed. Silence is streamed between turns so the voice
session stays active.

--text:  send the user turns as cross-modal text input while streaming silence.
--audio: live conversation over the local microphone and speakers. Needs
         `uv sync --group voice-local` and a headset; say "stop" or press Ctrl+C to end.
--debug: show strands SDK debug logs.

Nova Sonic only answers inside an active voice session: an audio content block must be open
(Nova 2 Sonic input-events docs: cross-modal text is "text messages during an active voice
session"). A text-only session sits idle forever, which is why silence is streamed in both
scripted modes.

Pass criteria: a non-empty final assistant transcript and spoken audio for both turns, and a
clean stop. In speech mode the model's transcript of each synthesized user turn must be non-empty.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import boto3
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BedrockNovaSonicModel
from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiConnectionStartEvent,
    BidiErrorEvent,
    BidiResponseCompleteEvent,
    BidiTranscriptStreamEvent,
    BidiUsageEvent,
)

from _common import AWS_PROFILE, MODEL_VOICE, REGION, fail, info, ok, passed, step

SYSTEM_PROMPT = (
    "You are Doorstep, a warm neighbour check-in assistant, running a connectivity test. "
    "Keep every reply to one short sentence."
)
TURNS = [
    "Hello, this is a smoke test. Can you hear me?",
    "Great. What is two plus two?",
]
TURN_TIMEOUT_S = 60
SAMPLE_RATE = 16000
CHUNK_BYTES = 640  # 20 ms of 16 kHz, 16-bit mono
CHUNK_SECONDS = 0.02
SILENCE = bytes(CHUNK_BYTES)


def build_model() -> BedrockNovaSonicModel:
    # The model takes either `boto_session` or `region`, not both; the session carries the region.
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
    return BedrockNovaSonicModel(
        model_id=MODEL_VOICE,
        boto_session=session,
        audio={"voice": "tiffany"},
    )


def synthesize(text: str, workdir: Path, index: int) -> bytes:
    """Return 16 kHz PCM16 mono audio for `text`, using macOS `say` and `afconvert`."""
    aiff = workdir / f"turn{index}.aiff"
    wav = workdir / f"turn{index}.wav"
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(aiff), str(wav)],
        check=True,
    )
    with wave.open(str(wav), "rb") as w:
        layout = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        if layout != (1, 2, SAMPLE_RATE):
            fail(f"unexpected audio layout {layout} from afconvert")
        return w.readframes(w.getnframes())


class AudioFeeder:
    """Streams 20 ms audio chunks to the agent at real-time pace: queued speech, else silence."""

    def __init__(self, agent: BidiAgent) -> None:
        self._agent = agent
        self._pending: deque[bytes] = deque()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def speak(self, pcm: bytes) -> None:
        for i in range(0, len(pcm), CHUNK_BYTES):
            self._pending.append(pcm[i : i + CHUNK_BYTES].ljust(CHUNK_BYTES, b"\x00"))

    async def _run(self) -> None:
        while not self._stop.is_set():
            data = self._pending.popleft() if self._pending else SILENCE
            await self._agent.send(
                BidiAudioInputEvent(
                    audio=base64.b64encode(data).decode(),
                    format="pcm",
                    sample_rate=SAMPLE_RATE,
                    channels=1,
                )
            )
            await asyncio.sleep(CHUNK_SECONDS)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task


@dataclass
class TurnResult:
    user_text: str = ""
    assistant_text: str = ""
    audio_chunks: int = 0


async def one_turn(
    agent: BidiAgent, feeder: AudioFeeder, text: str, pcm: bytes | None
) -> TurnResult:
    """Deliver one user turn as speech (pcm given) or cross-modal text, then collect the reply."""
    result = TurnResult()
    if pcm is not None:
        feeder.speak(pcm)
    else:
        await agent.send(text)

    async def consume() -> None:
        async for event in agent.receive():
            if isinstance(event, BidiConnectionStartEvent):
                info(f"connection open: id={event.connection_id} model={event.model}")
            elif isinstance(event, BidiTranscriptStreamEvent):
                transcript = (event.text or event.current_transcript or "").strip()
                stage = "final" if event.is_final else "partial"
                info(f"transcript ({event.role}, {stage}): {transcript[:120]}")
                if event.is_final and event.role == "assistant":
                    result.assistant_text = transcript
                elif event.is_final and event.role == "user":
                    result.user_text = transcript
            elif isinstance(event, BidiAudioStreamEvent):
                result.audio_chunks += 1
                if result.audio_chunks == 1:
                    info(f"first audio chunk back: {event.format} {event.sample_rate} Hz")
            elif isinstance(event, BidiUsageEvent):
                pass
            elif isinstance(event, BidiErrorEvent):
                raise RuntimeError(f"{type(event.error).__name__}: {event.error} {event.details}")
            elif isinstance(event, BidiResponseCompleteEvent):
                info(f"response complete: stop_reason={event.stop_reason}")
                if event.stop_reason != "tool_use":
                    break

    await asyncio.wait_for(consume(), timeout=TURN_TIMEOUT_S)
    return result


async def scripted_mode(speech: bool) -> None:
    label = "synthesized-speech" if speech else "cross-modal text"
    step(f"Creating BidiAgent on {MODEL_VOICE} (profile {AWS_PROFILE}, region {REGION})")
    pcm_turns: list[bytes | None] = [None] * len(TURNS)
    if speech:
        if not (shutil.which("say") and shutil.which("afconvert")):
            fail("speech mode needs macOS `say` and `afconvert`; use --text elsewhere", code=2)
        step("Synthesizing the user turns with macOS `say`")
        with tempfile.TemporaryDirectory() as tmp:
            pcm_turns = [synthesize(t, Path(tmp), i) for i, t in enumerate(TURNS, start=1)]
        for i, pcm in enumerate(pcm_turns, start=1):
            info(f"turn {i}: {len(pcm or b'') / (SAMPLE_RATE * 2):.1f}s of speech")

    agent = BidiAgent(model=build_model(), system_prompt=SYSTEM_PROMPT)
    feeder = AudioFeeder(agent)
    step("Starting the session; streaming silence so the voice session stays active")
    await agent.start()
    feeder.start()
    results: list[TurnResult] = []
    try:
        await asyncio.sleep(0.5)
        for i, text in enumerate(TURNS, start=1):
            step(f"Turn {i} ({label}): {text!r}")
            result = await one_turn(agent, feeder, text, pcm_turns[i - 1])
            info(
                f"turn {i}: {result.audio_chunks} audio chunks back, "
                f"reply {len(result.assistant_text)} chars"
            )
            results.append(result)
    except TimeoutError:
        fail(f"no completed response within {TURN_TIMEOUT_S}s")
    except Exception as exc:  # noqa: BLE001 - a smoke test reports any failure
        fail(f"{type(exc).__name__}: {exc}")
    finally:
        step("Stopping the audio stream and the session")
        await feeder.stop()
        await agent.stop()
    ok("session stopped cleanly")

    replies = [r.assistant_text for r in results]
    if len(results) != len(TURNS) or not all(replies):
        fail(f"expected {len(TURNS)} non-empty assistant transcripts, got {replies!r}")
    if speech and not all(r.user_text for r in results):
        fail(f"the model did not transcribe every user turn: {[r.user_text for r in results]!r}")
    if not all(r.audio_chunks for r in results):
        fail("no spoken audio came back for at least one turn")
    passed(f"Nova 2 Sonic BidiAgent on {MODEL_VOICE}: two {label} turns answered with speech")


async def audio_mode() -> None:
    try:
        from strands.experimental.bidi.io import BidiAudioIO, BidiTextIO
        from strands_tools import stop
    except ImportError as exc:
        fail(f"{exc}. Install the local audio group: uv sync --group voice-local", code=2)

    step(f"Live voice conversation on {MODEL_VOICE}. Use a headset. Say 'stop' or press Ctrl+C.")
    agent = BidiAgent(model=build_model(), system_prompt=SYSTEM_PROMPT, tools=[stop])
    audio_io = BidiAudioIO()
    text_io = BidiTextIO()
    try:
        await agent.run(inputs=[audio_io.input()], outputs=[audio_io.output(), text_io.output()])
    except KeyboardInterrupt:
        pass
    passed("voice conversation ended (manual check: you heard the agent and saw transcripts)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--text", action="store_true", help="send the user turns as text instead of speech"
    )
    parser.add_argument(
        "--audio", action="store_true", help="use the local microphone and speakers"
    )
    parser.add_argument("--debug", action="store_true", help="show strands SDK debug logs")
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
        logging.getLogger("strands").setLevel(logging.DEBUG)
    if args.audio:
        asyncio.run(audio_mode())
    else:
        asyncio.run(scripted_mode(speech=not args.text))


if __name__ == "__main__":
    main()
