"""Voice check-ins offline: tokens, the codec, prompt reuse, and a call session on a fake Sonic.

The session tests drive `VoiceCheckin` with a fake bidi agent that replays Nova-shaped events
(the real Strands event classes) and a fake line. They pin the behaviours that matter most:

* a red flag pages the captain **before** the call ends, whether the agent flags it or only the
  resident's own words do (the deterministic backstop, live);
* the call is held open until the page is out, and gives up after the wait;
* barge-in clears playback; hang-up, time cap and silence cap all still report the attempt.
"""

from __future__ import annotations

import array
import asyncio
import base64
import time
import warnings
from types import SimpleNamespace
from typing import Any

import pytest
from strands import Agent
from strands.experimental.bidi.types.events import (
    BidiAudioStreamEvent,
    BidiInterruptionEvent,
    BidiTranscriptStreamEvent,
)

from conftest import ROOT, make_ctx
from doorstep_agent.agents.checkin_text import CALL_CONNECTED, protocol_prompt, system_prompt
from doorstep_agent.profiles import load_profile
from doorstep_agent.tools import flag_urgent
from doorstep_voice import tokens
from doorstep_voice.audio import pcm16_to_ulaw, ulaw_to_pcm16
from doorstep_voice.session import CallSetup, VoiceCheckin
from helpers.scripted_model import ScriptedModel, ToolCall, Turn

SECRET = "voice-secret-for-tests"
FIXTURES_PROFILES = ROOT / "tests" / "fixtures" / "profiles"


# --- tokens -----------------------------------------------------------------------------------


def _mint(**kw: Any) -> tuple[str, dict[str, Any]]:
    args = {
        "incident_id": "inc-test",
        "resident_id": "r04",
        "channel": "browser",
        "mode": "drill",
        "now": 1_000_000.0,
    } | kw
    return tokens.mint(SECRET, **args)


def test_a_token_round_trips_and_names_everything_the_call_needs() -> None:
    token, claims = _mint()
    got = tokens.verify(token, SECRET, channel="browser", now=1_000_030.0)
    assert got == claims
    assert {got["inc"], got["res"], got["ch"], got["mode"]} == {
        "inc-test",
        "r04",
        "browser",
        "drill",
    }
    assert got["exp"] - got["iat"] == 60 and len(got["jti"]) == 24


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"), "bad signature"),
        (lambda t: "x" + t, "bad signature"),
        (lambda t: t.replace(".", ""), "malformed"),
        (lambda t: "", "malformed"),
    ],
)
def test_a_tampered_token_is_refused(mutate: Any, message: str) -> None:
    token, _ = _mint()
    with pytest.raises(tokens.TokenError, match=message):
        tokens.verify(mutate(token), SECRET, now=1_000_001.0)


def test_a_token_signed_with_another_secret_is_refused() -> None:
    token, _ = _mint()
    with pytest.raises(tokens.TokenError, match="bad signature"):
        tokens.verify(token, "another-secret", now=1_000_001.0)


def test_expiry_channel_and_future_tokens_are_refused() -> None:
    token, _ = _mint()
    with pytest.raises(tokens.TokenError, match="expired"):
        tokens.verify(token, SECRET, now=1_000_060.0)
    with pytest.raises(tokens.TokenError, match="another channel"):
        tokens.verify(token, SECRET, channel="phone", now=1_000_001.0)
    future, _ = _mint(now=2_000_000.0)
    with pytest.raises(tokens.TokenError, match="future"):
        tokens.verify(future, SECRET, now=1_000_001.0)
    with pytest.raises(tokens.TokenError):
        _mint(ttl_seconds=3600)
    with pytest.raises(tokens.TokenError):
        _mint(resident_id="../r04")
    with pytest.raises(tokens.TokenError, match="no signing secret"):
        tokens.mint("", incident_id="inc-test", resident_id="r04", channel="browser", mode="drill")


# --- audio ------------------------------------------------------------------------------------


def test_mu_law_matches_the_reference_codec_exactly() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop
    every_byte = bytes(range(256))
    assert ulaw_to_pcm16(every_byte) == audioop.ulaw2lin(every_byte, 2)
    every_sample = array.array("h", range(-32768, 32768)).tobytes()
    assert pcm16_to_ulaw(every_sample) == audioop.lin2ulaw(every_sample, 2)
    assert pcm16_to_ulaw(b"\x00\x00\x01") == pcm16_to_ulaw(b"\x00\x00")  # odd byte dropped


def test_one_phone_frame_converts_well_inside_its_20_ms() -> None:
    frame = bytes(320)
    started = time.perf_counter()
    for _ in range(100):
        ulaw_to_pcm16(pcm16_to_ulaw(frame))
    assert (time.perf_counter() - started) / 100 < 0.002


# --- the protocol is shared --------------------------------------------------------------------


def test_the_voice_prompt_is_the_text_script_plus_live_call_rules() -> None:
    ctx = make_ctx()
    for resident in ctx.store.residents():
        text = system_prompt(ctx, resident)
        voice = protocol_prompt(ctx.profile, ctx.org, resident, channel="voice")
        script = text.rsplit("Reply with only the words", 1)[0]
        assert voice.startswith(script), resident.id
        assert CALL_CONNECTED in voice and "live voice call" in voice
        for q in ctx.profile.checkin_questions:
            assert q.text(resident.language) in voice


def test_a_profile_edit_changes_what_the_voice_agent_asks() -> None:
    ctx = make_ctx()
    resident = ctx.store.resident("r04")
    dummy = load_profile(FIXTURES_PROFILES / "dummy.yaml")
    voice = protocol_prompt(dummy, ctx.org, resident, channel="voice")
    assert "Is your boat ready?" in voice
    assert "Purple smoke indoors" in voice and "Close the windows and count your sandbags." in voice
    for q in ctx.profile.questions_text("en"):
        assert q not in voice


def test_flag_urgent_calls_the_live_page_callback_without_a_run_context() -> None:
    """The voice process has no RunContext; the real tool still records and pages."""
    paged: list[str] = []
    call: dict[str, Any] = {"answers": {}, "urgent": [], "ended": False}
    model = ScriptedModel(
        [Turn(tool_calls=[ToolCall("flag_urgent", {"reason": "says she is dizzy"}, "tu-1")])]
    )
    agent = Agent(model=model, tools=[flag_urgent], callback_handler=None)
    agent("go", invocation_state={"call": call, "on_urgent": paged.append})
    assert paged == ["says she is dizzy"] and call["urgent"] == ["says she is dizzy"]


# --- a call on a fake Sonic --------------------------------------------------------------------


def transcript(role: str, text: str) -> BidiTranscriptStreamEvent:
    return BidiTranscriptStreamEvent(
        delta={"text": text}, text=text, role=role, is_final=True, current_transcript=text
    )


def audio(seconds: float = 0.1) -> BidiAudioStreamEvent:
    pcm = bytes(int(16000 * seconds) * 2)
    return BidiAudioStreamEvent(
        audio=base64.b64encode(pcm).decode(), format="pcm", sample_rate=16000, channels=1
    )


class FakeAgent:
    """Replays events; a step may be an event, a pause, or something done to the call state."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps
        self.sent: list[Any] = []
        self.state: dict[str, Any] = {}
        self.stopped = False
        self.model = SimpleNamespace(_connection_id=None)

    async def start(self, invocation_state: dict[str, Any]) -> None:
        self.state = invocation_state

    async def send(self, event: Any) -> None:
        self.sent.append(event)

    async def receive(self):  # noqa: ANN201
        for step in self.steps:
            if isinstance(step, (int, float)):
                await asyncio.sleep(step)
            elif callable(step):
                result = step(self.state)
                if asyncio.iscoroutine(result):
                    await result
            else:
                yield step
        await asyncio.Event().wait()

    async def stop(self) -> None:
        self.stopped = True


def end_call(state: dict[str, Any]) -> None:
    state["call"]["ended"] = True
    state["call"]["summary"] = "done"


class FakeLine:
    def __init__(self, hang_up_after: float | None = None) -> None:
        self.log: list[Any] = []
        self.events: list[dict[str, Any]] = []
        self.hang_up_at = None if hang_up_after is None else time.monotonic() + hang_up_after

    async def receive(self) -> bytes | None:
        await asyncio.sleep(0.02)
        if self.hang_up_at is not None and time.monotonic() >= self.hang_up_at:
            return None
        return bytes(640)

    async def play(self, pcm: bytes) -> None:
        self.log.append("play")

    async def clear(self) -> None:
        self.log.append("clear")

    async def drained(self, timeout: float) -> None:
        self.log.append("drained")

    async def notify(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def close(self, reason: str) -> None:
        self.log.append(("close", reason))


class FakeSink:
    def __init__(self, delivered: list[bool] | None = None) -> None:
        self.log: list[Any] = []
        self.urgent_events: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self.delivered = list(delivered or [True])

    async def urgent(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        self.log.append("urgent")
        self.urgent_events.append(event)

    async def attempt(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        self.log.append("attempt")
        self.attempts.append(event)

    async def page_delivered(self, claims: dict[str, Any]) -> bool:
        self.log.append("page_check")
        return self.delivered.pop(0) if len(self.delivered) > 1 else self.delivered[0]


def setup(**kw: Any) -> CallSetup:
    ctx = make_ctx()
    _, claims = _mint()
    args = {
        "claims": claims,
        "resident": ctx.store.resident("r04"),
        "profile": ctx.profile,
        "org": ctx.org,
        "channel": "browser",
        "input_rate": 16000,
        "output_rate": 16000,
        "max_seconds": 10.0,
        "silence_seconds": 5.0,
        "page_wait_seconds": 3.0,
    } | kw
    return CallSetup(**args)


async def run_call(
    steps: list[Any], line: FakeLine | None = None, sink: FakeSink | None = None, **kw: Any
):
    line, sink = line or FakeLine(), sink or FakeSink()
    agent = FakeAgent(steps)
    session = VoiceCheckin(setup(**kw), sink, agent_factory=lambda s, prompt: agent)
    record = await asyncio.wait_for(session.run(line), 15)
    return record, line, sink, agent


async def test_the_residents_own_words_page_the_captain_before_the_call_ends() -> None:
    sink = FakeSink(delivered=[False, True])
    line = FakeLine()
    closed_before_page: list[bool] = []

    async def after_page(state: dict[str, Any]) -> None:
        closed_before_page.append(any(isinstance(x, tuple) for x in line.log))

    record, line, sink, agent = await run_call(
        [
            audio(),
            transcript("assistant", "How are you feeling right now?"),
            transcript("user", "well honestly i feel dizzy and"),
            transcript("user", "confused"),
            0.2,
            after_page,
            transcript("assistant", "I'm getting someone to check on you right now."),
            end_call,
        ],
        line=line,
        sink=sink,
    )
    assert sink.log[0] == "urgent", "the page is the first thing the coordinator hears"
    assert sink.urgent_events[0]["source"] == "backstop"
    assert "dizzy" in sink.urgent_events[0]["reason"]
    assert closed_before_page == [False], "the page went out while the call was still open"
    assert sink.log.count("page_check") >= 2, "the line was held until the page was delivered"
    assert sink.log[-1] == "attempt"
    attempt = sink.attempts[0]
    assert attempt["page_delivered"] is True and attempt["end_reason"] == "completed"
    assert attempt["urgent_source"] == "backstop"
    assert {"speaker": "resident", "text": "well honestly i feel dizzy and confused"} in attempt[
        "transcript"
    ]
    texts = [x for x in agent.sent if isinstance(x, str)]
    assert texts == [CALL_CONNECTED], "nothing is injected into the call on a red flag"
    assert agent.stopped


async def test_the_agents_flag_pages_from_the_tool_thread() -> None:
    async def flag_from_tool_thread(state: dict[str, Any]) -> None:
        state["call"]["urgent"].append("chest pain")
        await asyncio.to_thread(state["on_urgent"], "chest pain")

    record, line, sink, agent = await run_call(
        [transcript("user", "my chest hurts a lot"), flag_from_tool_thread, 0.1, end_call]
    )
    assert sink.urgent_events[0]["source"] in ("agent", "backstop")
    assert sink.log.count("urgent") == 1, "one page per call"
    assert sink.attempts[0]["urgent_flags"] == ["chest pain"]


async def test_a_flag_the_backstop_cannot_hear_still_pages_through_the_agent() -> None:
    async def flag(state: dict[str, Any]) -> None:
        state["call"]["urgent"].append("sounds unwell")
        await asyncio.to_thread(state["on_urgent"], "sounds unwell")

    _, _, sink, agent = await run_call(
        [transcript("user", "i'm okay i think"), flag, 0.1, end_call]
    )
    assert sink.urgent_events[0]["source"] == "agent"
    assert not any("red flag" in str(s) for s in agent.sent)


async def test_negated_words_do_not_page() -> None:
    _, _, sink, _ = await run_call(
        [transcript("user", "no i'm not dizzy at all thank you"), 0.1, end_call]
    )
    assert "urgent" not in sink.log and sink.attempts[0]["urgent_sent_at"] is None


async def test_barge_in_clears_playback() -> None:
    record, line, _, _ = await run_call(
        [audio(), BidiInterruptionEvent(reason="user_speech"), end_call]
    )
    assert line.log.index("clear") > line.log.index("play")
    assert record.interruptions == 1


async def test_a_hang_up_still_reports_the_attempt() -> None:
    record, line, sink, agent = await run_call(
        [transcript("assistant", "Hi, is this Dolores?")], line=FakeLine(hang_up_after=0.3)
    )
    assert record.end_reason == "caller_hung_up"
    assert sink.attempts and sink.attempts[0]["transcript"][0]["speaker"] == "agent"
    assert agent.stopped and ("close", "caller_hung_up") in line.log


async def test_the_time_cap_ends_the_call_and_asks_the_agent_to_close_first() -> None:
    record, _, sink, agent = await run_call(
        [audio()] + [0.05, audio()] * 30,
        max_seconds=1.0,
        closing_warning_seconds=0.5,
    )
    assert record.end_reason == "time_limit"
    assert any("Time is nearly up" in str(s) for s in agent.sent)
    assert sink.attempts


async def test_silence_ends_the_call() -> None:
    record, _, sink, _ = await run_call([], silence_seconds=0.4)
    assert record.end_reason == "silence" and sink.attempts


async def test_the_page_wait_gives_up_and_says_so() -> None:
    sink = FakeSink(delivered=[False])
    record, _, sink, _ = await run_call(
        [transcript("user", "i can't get up off the floor"), 0.1, end_call],
        sink=sink,
        page_wait_seconds=0.6,
    )
    assert record.page_delivered is False and sink.attempts[0]["page_delivered"] is False


async def test_the_call_starts_by_telling_the_agent_the_line_is_open() -> None:
    _, line, _, agent = await run_call([end_call])
    assert agent.sent[0] == CALL_CONNECTED
    assert line.events[0] == {"type": "status", "status": "connected"}
