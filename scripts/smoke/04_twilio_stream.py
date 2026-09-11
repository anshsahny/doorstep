"""Smoke test 04: a call from the Twilio `doorstep` subaccount streams audio to a local WebSocket.

Flow:
  1. Start a WebSocket server on 127.0.0.1:8765 that logs Twilio Media Streams events.
  2. Start `ngrok http 8765` (or reuse a running tunnel); read the public URL from its local API.
  3. Create the call on the SUBACCOUNT with inline TwiML: <Say> greeting, then <Connect><Stream>
     to wss://<ngrok>/media with a <Parameter>, then <Say> goodbye and <Hangup/>.
  4. After a few seconds of inbound audio the server closes the socket, which ends <Connect> and
     lets the goodbye play. A REST hang-up is the safety net; the call also has a 60 s time limit.

Pass criteria: `start` event received, at least 50 `media` frames, and the call ends with status
`completed`. A `stop` event only arrives when Twilio closes the stream first (for example the callee
hangs up); it is logged when seen but not required, because this server closes the stream itself.

Safety: SMOKE_CALL_TO must be listed in CALL_ALLOWLIST; the credentials must belong to a
subaccount (owner_account_sid differs from its own SID); the from-number must be owned by that
subaccount. Nothing else is dialled.
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse
from websockets.asyncio.server import ServerConnection, serve

from _common import e164, env, fail, info, ok, passed, redact, step

WS_PORT = 8765
NGROK_API = "http://127.0.0.1:4040/api/tunnels"
MEDIA_SECONDS = 3.0  # Twilio sends 20 ms frames, so 50 frames per second
MIN_FRAMES = 50
CALL_TIME_LIMIT_S = 60
RING_TIMEOUT_S = 30
OVERALL_TIMEOUT_S = 120


@dataclass
class StreamLog:
    counts: Counter[str] = field(default_factory=Counter)
    start: dict[str, Any] | None = None
    stop_seen: bool = False
    frames: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)


async def handle_twilio(ws: ServerConnection, log: StreamLog) -> None:
    info(f"WebSocket connection opened (path {ws.request.path if ws.request else '?'})")
    target_frames = int(MEDIA_SECONDS * 50)
    try:
        async for raw in ws:
            msg = json.loads(raw)
            event = msg.get("event", "?")
            log.counts[event] += 1
            if event == "connected":
                info(f"connected: protocol={msg.get('protocol')} version={msg.get('version')}")
            elif event == "start":
                log.start = msg.get("start", {})
                s = log.start
                info(
                    f"start: streamSid={s.get('streamSid')} callSid={s.get('callSid')} "
                    f"tracks={s.get('tracks')} mediaFormat={s.get('mediaFormat')} "
                    f"customParameters={s.get('customParameters')}"
                )
            elif event == "media":
                log.frames += 1
                media = msg.get("media", {})
                if log.frames == 1:
                    payload = base64.b64decode(media.get("payload", ""))
                    info(
                        f"first media frame: track={media.get('track')} chunk={media.get('chunk')} "
                        f"timestamp={media.get('timestamp')}ms payload={len(payload)} bytes"
                    )
                if log.frames == target_frames:
                    ok(
                        f"{log.frames} media frames (~{MEDIA_SECONDS:.0f}s of audio); "
                        "closing the stream"
                    )
                    await ws.close()
                    break
            elif event == "stop":
                log.stop_seen = True
                info(f"stop: {msg.get('stop')}")
                break
            else:
                info(f"{event}: {json.dumps(msg)[:200]}")
    finally:
        log.done.set()


def ngrok_public_url(proc_started: bool) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            tunnels = httpx.get(NGROK_API, timeout=3).json().get("tunnels", [])
        except (httpx.HTTPError, ValueError):
            tunnels = []
        for t in tunnels:
            addr = str(t.get("config", {}).get("addr", ""))
            if addr.endswith(f":{WS_PORT}") and str(t.get("public_url", "")).startswith("https://"):
                return t["public_url"]
        if tunnels and not proc_started:
            fail(f"ngrok is running but not for port {WS_PORT}; stop it and rerun", code=2)
        time.sleep(1)
    fail(
        "ngrok tunnel did not come up within 20s "
        "(is the authtoken configured? run `ngrok config check`)"
    )
    return ""  # unreachable


def start_ngrok() -> tuple[subprocess.Popen[bytes] | None, str]:
    try:
        existing = httpx.get(NGROK_API, timeout=2).json().get("tunnels", [])
    except (httpx.HTTPError, ValueError):
        existing = []
    if existing:
        info("reusing the ngrok agent already running on 127.0.0.1:4040")
        return None, ngrok_public_url(proc_started=False)
    proc = subprocess.Popen(
        ["ngrok", "http", str(WS_PORT), "--log", "stdout", "--log-format", "json"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, ngrok_public_url(proc_started=True)


def twilio_client_for_subaccount() -> tuple[Client, str]:
    sid = env("TWILIO_SUBACCOUNT_SID")
    token = env("TWILIO_SUBACCOUNT_TOKEN")
    if not sid.startswith("AC"):
        fail("TWILIO_SUBACCOUNT_SID must start with AC", code=2)
    client = Client(sid, token)
    try:
        account = client.api.v2010.accounts(sid).fetch()
    except TwilioRestException as exc:
        fail(f"Twilio auth failed for {redact(sid)}: {exc.msg}", code=2)
    if account.owner_account_sid == account.sid:
        fail(
            f"{redact(sid)} is a PARENT account (owner_account_sid == sid). "
            "Use the `doorstep` subaccount SID and token.",
            code=2,
        )
    ok(
        f"subaccount {redact(sid)} '{account.friendly_name}' status={account.status} "
        f"type={account.type} owner={redact(account.owner_account_sid)}"
    )
    return client, sid


def build_twiml(wss_url: str) -> str:
    vr = VoiceResponse()
    vr.say(
        "Hello. This is the Doorstep smoke test. Please say a few words, "
        "and I will hang up in a few seconds."
    )
    connect = Connect()
    stream = Stream(url=wss_url)
    stream.parameter(name="token", value="smoke")
    stream.parameter(name="purpose", value="phase0-smoke")
    connect.append(stream)
    vr.append(connect)
    vr.say("Thanks. Goodbye.")
    vr.hangup()
    return str(vr)


async def run() -> None:
    to_number = e164("SMOKE_CALL_TO")
    from_number = e164("TWILIO_FROM_NUMBER")
    allowlist = {n.strip() for n in env("CALL_ALLOWLIST").split(",") if n.strip()}
    if to_number not in allowlist:
        fail(f"{to_number} is not in CALL_ALLOWLIST; refusing to dial", code=2)
    ok(f"callee {to_number} is on the allowlist")

    step("Checking the Twilio credentials belong to a subaccount")
    client, _sid = twilio_client_for_subaccount()
    owned = client.incoming_phone_numbers.list(phone_number=from_number, limit=1)
    if not owned:
        fail(f"{from_number} is not a number owned by this subaccount", code=2)
    ok(f"from-number {from_number} is owned by the subaccount")

    log = StreamLog()
    step(f"Starting WebSocket server on 127.0.0.1:{WS_PORT}")
    server = await serve(lambda ws: handle_twilio(ws, log), "127.0.0.1", WS_PORT)

    step("Starting ngrok tunnel")
    ngrok_proc, public_url = start_ngrok()
    wss_url = public_url.replace("https://", "wss://") + "/media"
    ok(f"stream URL {wss_url}")

    call_sid: str | None = None
    final_status: str | None = None
    try:
        step(f"Placing the call from {from_number} to {to_number} (answer your phone)")
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            twiml=build_twiml(wss_url),
            time_limit=CALL_TIME_LIMIT_S,
            timeout=RING_TIMEOUT_S,
        )
        call_sid = call.sid
        info(f"call {call.sid} status={call.status}")

        deadline = time.monotonic() + OVERALL_TIMEOUT_S
        last_status = call.status
        while not log.done.is_set() and time.monotonic() < deadline:
            try:
                await asyncio.wait_for(log.done.wait(), timeout=5)
            except TimeoutError:
                pass
            status = (await asyncio.to_thread(client.calls(call.sid).fetch)).status
            if status != last_status:
                info(f"call status: {status}")
                last_status = status
            if status in {"busy", "failed", "no-answer", "canceled"}:
                fail(f"call ended with status {status} before any media arrived")
            if status == "completed" and not log.done.is_set():
                await asyncio.sleep(2)
                break

        # If Twilio ended the stream first, its `stop` message may still be in flight.
        if not log.stop_seen:
            await asyncio.sleep(3)
    finally:
        if call_sid:
            final = client.calls(call_sid).fetch()
            if final.status in {"queued", "ringing", "in-progress"}:
                info(f"hanging up via REST (status was {final.status})")
                client.calls(call_sid).update(status="completed")
                final = client.calls(call_sid).fetch()
            final_status = final.status
            info(f"final call status={final_status} duration={final.duration}s")
        server.close()
        await server.wait_closed()
        if ngrok_proc:
            ngrok_proc.terminate()

    info(f"event counts: {dict(log.counts)}")
    if log.start is None:
        fail("no `start` event received")
    if log.frames < MIN_FRAMES:
        fail(f"only {log.frames} media frames received (need {MIN_FRAMES})")
    if log.stop_seen:
        ok("`stop` event received (Twilio ended the stream)")
    elif final_status == "completed":
        ok("stream closed by this server; the call then completed normally")
    else:
        fail(f"no `stop` event and the call ended with status {final_status}")
    fmt = log.start.get("mediaFormat", {})
    passed(
        f"Twilio subaccount call streamed {log.frames} frames of "
        f"{fmt.get('encoding')} @ {fmt.get('sampleRate')} Hz to the local WebSocket"
    )


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        fail("interrupted")
