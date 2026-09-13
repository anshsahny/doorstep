"""Phase 4 spikes S1-S3: the real check-in prompt and tools on Nova 2 Sonic, spoken to by `say`.

S1 (default): 16 kHz PCM in and out. The agent is kicked off by a cross-modal text line, the
    resident's turns are synthesized speech streamed at real-time pace with silence between.
    One turn contains a red-flag phrase. Measures: user transcript -> flag_urgent latency, and
    whether audio keeps flowing while `flag_urgent` deliberately blocks for 5 s (async tools).
S2 (--rate 8000): the same at 8 kHz in and out, with the resident audio pushed through mu-law
    encode/decode first, as a Twilio call would deliver it.
S3 (--barge-in): the resident talks over the agent's greeting; records BidiInterruptionEvent
    timing and how far ahead of real time Sonic streams its audio.

Costs a few cents of Nova 2 Sonic. Prints every event with a timestamp; exit 0 when the tools
were called and flag_urgent fired before the session ended.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop  # noqa: F401 - Python 3.12 still ships it; the spike uses it as the reference codec
import base64
import json
import logging
import subprocess
import sys
import tempfile
import time
import wave
from collections import deque
from pathlib import Path

import boto3
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BedrockNovaSonicModel
from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiErrorEvent,
    BidiInterruptionEvent,
    BidiResponseCompleteEvent,
    BidiResponseStartEvent,
    BidiTranscriptStreamEvent,
    BidiUsageEvent,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import make_ctx  # noqa: E402
from doorstep_agent.agents.checkin_text import system_prompt  # noqa: E402
from doorstep_agent.tools import CHECKIN_TOOLS  # noqa: E402

T0 = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:6.2f}s] {msg}", flush=True)


def synth(text: str, rate: int, workdir: Path, name: str) -> bytes:
    aiff, wav = workdir / f"{name}.aiff", workdir / f"{name}.wav"
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{rate}", "-c", "1", str(aiff), str(wav)],
        check=True,
    )
    with wave.open(str(wav), "rb") as w:
        return w.readframes(w.getnframes())


class Feeder:
    def __init__(self, agent: BidiAgent, rate: int, mulaw: bool) -> None:
        self.agent, self.rate, self.mulaw = agent, rate, mulaw
        self.chunk = rate // 50 * 2  # 20 ms of PCM16
        self.pending: deque[bytes] = deque()
        self.stop = asyncio.Event()

    def speak(self, pcm: bytes) -> None:
        if self.mulaw:  # what a phone line does to it
            pcm = audioop.ulaw2lin(audioop.lin2ulaw(pcm, 2), 2)
        for i in range(0, len(pcm), self.chunk):
            self.pending.append(pcm[i : i + self.chunk].ljust(self.chunk, b"\0"))

    def busy(self) -> bool:
        return bool(self.pending)

    async def run(self) -> None:
        silence = bytes(self.chunk)
        next_at = time.monotonic()
        while not self.stop.is_set():
            data = self.pending.popleft() if self.pending else silence
            await self.agent.send(
                BidiAudioInputEvent(
                    audio=base64.b64encode(data).decode(),
                    format="pcm",
                    sample_rate=self.rate,  # type: ignore[arg-type]
                    channels=1,
                )
            )
            next_at += 0.02
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))


SCRIPTS = {
    "urgent": [
        "Yes, this is Dolores. Sure, go ahead.",
        "Well, honestly I feel dizzy and confused. I'm not sure what day it is.",
        "Okay. Thank you.",
    ],
    "ok": [
        "Yes, this is Dolores. Sure, go ahead.",
        "I'm feeling fine, thanks.",
        "Yes, the air conditioner is on and it's nice and cool in here.",
        "Yes, I have plenty of water and my medicines.",
        "No thank you, I'm all set today.",
        "Okay, thank you, bye.",
        "Bye.",
    ],
}
RAW_USAGE: dict = {}


class _UsageTap(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "nova usage event" in msg and "usage_details=<" in msg:
            try:
                RAW_USAGE.update(json.loads(msg.split("usage_details=<", 1)[1].rsplit(">", 1)[0]))
            except ValueError:
                pass


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=int, default=16000, choices=[8000, 16000])
    p.add_argument("--barge-in", action="store_true")
    p.add_argument("--max-seconds", type=float, default=90)
    p.add_argument("--script", choices=list(SCRIPTS), default="urgent")
    args = p.parse_args()
    RESIDENT_LINES = SCRIPTS[args.script]
    tap_logger = logging.getLogger("strands.experimental.bidi.models.bedrock")
    tap_logger.setLevel(logging.DEBUG)
    tap_logger.addHandler(_UsageTap())
    tap_logger.propagate = False
    mulaw = args.rate == 8000

    ctx = make_ctx()
    resident = ctx.store.resident("r04")
    prompt = system_prompt(ctx, resident)

    marks: dict[str, float] = {}
    tool_calls: list[tuple[float, str, dict]] = []
    audio_during_tool = {"n": 0}
    in_tool = {"flag": False}

    # flag_urgent's only I/O is the audit record; make it block for 5 s like a slow page would.
    real_record = ctx.audit.record

    def slow_record(**kw):  # type: ignore[no-untyped-def]
        if str(kw.get("reason", "")).startswith("flag_urgent"):
            in_tool["flag"] = True
            marks.setdefault("flag_urgent_start", time.monotonic() - T0)
            log("flag_urgent tool body running (blocking 5 s)")
            time.sleep(5)
            in_tool["flag"] = False
            log("flag_urgent tool body done")
        return real_record(**kw)

    ctx.audit.record = slow_record  # type: ignore[method-assign]

    call: dict = {"answers": {}, "urgent": [], "ended": False, "summary": ""}
    state = ctx.invocation_state(resident_id=resident.id, actor="agent:checkin_voice", call=call)

    session = boto3.Session(profile_name="doorstep", region_name="us-east-1")
    model = BedrockNovaSonicModel(
        model_id="amazon.nova-2-sonic-v1:0",
        boto_session=session,
        audio={"input_rate": args.rate, "output_rate": args.rate, "voice": "tiffany"},
    )
    agent = BidiAgent(model=model, system_prompt=prompt, tools=CHECKIN_TOOLS)
    with tempfile.TemporaryDirectory() as tmp:
        lines = [synth(t, args.rate, Path(tmp), f"l{i}") for i, t in enumerate(RESIDENT_LINES)]
        barge = synth("Sorry, who did you say this is?", args.rate, Path(tmp), "barge")

    await agent.start(invocation_state=state)
    feeder = Feeder(agent, args.rate, mulaw)
    feed_task = asyncio.create_task(feeder.run())
    log(f"session started rate={args.rate} mulaw={mulaw} resident={resident.first_name}")
    await agent.send("[The phone call has just connected. Greet the resident now.]")
    marks["kick"] = time.monotonic() - T0

    usage: dict = {}
    barged = False
    play = {"until": 0.0, "spoke": False, "resp_bytes": 0}
    line_idx = 0

    async def turn_taker() -> None:
        nonlocal line_idx
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic() - T0
            if call["ended"] and now > play["until"] + 1.0:
                log("end_call recorded and closing line played; stopping")
                return
            if play["spoke"] and not feeder.busy() and now > play["until"] + 1.2:
                if line_idx < len(RESIDENT_LINES):
                    log(f"resident says: {RESIDENT_LINES[line_idx]!r}")
                    feeder.speak(lines[line_idx])
                    line_idx += 1
                    play["spoke"] = False

    taker = asyncio.create_task(turn_taker())
    try:
        async with asyncio.timeout(args.max_seconds):
            async for ev in agent.receive():
                now = time.monotonic() - T0
                if taker.done():
                    break
                if isinstance(ev, BidiResponseStartEvent):
                    play["resp_bytes"] = 0
                elif isinstance(ev, BidiAudioStreamEvent):
                    n = len(base64.b64decode(ev.audio))
                    secs = n / (args.rate * 2)
                    play["until"] = max(play["until"], now) + secs
                    play["resp_bytes"] += n
                    play["spoke"] = True
                    marks.setdefault("first_audio", now)
                    if in_tool["flag"]:
                        audio_during_tool["n"] += 1
                    if args.barge_in and not barged and play["resp_bytes"] > args.rate * 2 * 2:
                        barged = True
                        marks["barge_in_sent"] = now
                        marks["play_ahead_s"] = play["until"] - now
                        log(
                            "BARGE-IN: resident talks over the agent "
                            f"(playback ahead by {play['until'] - now:.1f}s)"
                        )
                        feeder.speak(barge)
                elif isinstance(ev, BidiTranscriptStreamEvent):
                    if ev.is_final or ev.role == "user":
                        log(f"transcript {ev.role} final={ev.is_final}: {ev.text!r}")
                    if ev.role == "user" and "dizzy" in (ev.text or "").lower():
                        marks.setdefault("red_flag_transcript", now)
                elif isinstance(ev, BidiInterruptionEvent):
                    marks.setdefault("interruption", now)
                    play["until"] = now  # a client would flush its playback queue here
                    log(f"INTERRUPTION reason={ev.reason}")
                elif isinstance(ev, BidiResponseCompleteEvent):
                    log(
                        f"response complete stop={ev.stop_reason} "
                        f"(playback until {play['until']:.1f}s)"
                    )
                elif isinstance(ev, BidiUsageEvent):
                    usage = dict(ev)
                elif isinstance(ev, BidiErrorEvent):
                    log(f"ERROR {ev.error} {ev.details}")
                    break
                elif type(ev).__name__ == "ToolUseStreamEvent":
                    tu = ev["current_tool_use"]
                    tool_calls.append((now, tu["name"], tu["input"]))
                    log(f"TOOL USE {tu['name']} {tu['input']}")
    except TimeoutError:
        log("timed out")
    finally:
        taker.cancel()
        feeder.stop.set()
        await feed_task
        await agent.stop()

    print("\n=== summary ===")
    print("tool calls:", [(round(t, 2), n) for t, n, _ in tool_calls])
    print("answers:", call["answers"], "urgent:", call["urgent"], "ended:", call["ended"])
    print("marks:", {k: round(v, 2) for k, v in marks.items()})
    if "red_flag_transcript" in marks and "flag_urgent_start" in marks:
        print(
            "red-flag transcript -> flag_urgent body:",
            round(marks["flag_urgent_start"] - marks["red_flag_transcript"], 2),
            "s",
        )
    print("audio chunks received while flag_urgent blocked:", audio_during_tool["n"])
    print("usage:", json.dumps({k: v for k, v in usage.items() if k != "type"}, default=str))
    print("raw nova usage:", json.dumps(RAW_USAGE))
    ok = bool(call["urgent"]) if args.script == "urgent" else len(call["answers"]) >= 3
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
