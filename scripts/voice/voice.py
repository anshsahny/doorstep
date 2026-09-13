"""Browser voice, operated from the terminal (Phase 4, Gate 4a).

    incident   start a drill whose residents wait for a voice call (nobody is simulated)
    page       serve web/voice-test on http://localhost:5174 (the mic needs a secure origin)
    e2e        a synthetic browser: real /voice/session link, real WebSocket, macOS `say` audio
    evidence   what the incident recorded for each voice call: timings, result, page, tokens

Examples:
    make voice-incident                       # prints the incident id and the page URL
    make voice-e2e ARGS="--incident <id> --resident r04 --script urgent"
    make voice-evidence ARGS=<id>
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import websockets  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from doorstep_agent.store import NotFound  # noqa: E402
from doorstep_agent.store_dynamo import DynamoBackend  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402

PAGE_DIR = ROOT / "web" / "voice-test"
PAGE_PORT = 5174
ALL_RESIDENTS = [f"r{i:02d}" for i in range(1, 13)]
# Nova 2 Sonic list prices (USD per 1M tokens), to be confirmed against Cost Explorer.
SONIC_PRICE = {"speech_in": 3.00, "speech_out": 12.00, "text_in": 0.33, "text_out": 2.75}

SCRIPTS = {
    "ok": [
        "Yes, speaking. Sure, go ahead.",
        "I'm feeling fine, thanks.",
        "Yes, the air conditioner is on and it's nice and cool in here.",
        "Yes, I have plenty of water and my medicines.",
        "No thank you, I'm all set today.",
        "Okay, thank you, bye.",
        "Bye.",
    ],
    "urgent": [
        "Yes, speaking. Go ahead.",
        "Well, honestly I feel dizzy and confused. I'm not sure what day it is.",
        "Okay. Thank you.",
        "Okay.",
    ],
}


def backend(aws: Any, out: dict[str, str]) -> DynamoBackend:
    return DynamoBackend(
        out["TableName"], "juniper-court", client=aws.client("dynamodb"), cache=False
    )


# --- incident ----------------------------------------------------------------------------------


def cmd_incident(args: argparse.Namespace) -> int:
    aws = session()
    out = outputs(aws)
    passcode = (dotenv_values(ROOT / ".env").get("CAPTAIN_PASSCODE") or "").strip()
    residents = args.residents.split(",") if args.residents else ALL_RESIDENTS
    reply = httpx.post(
        out["ApiUrl"].rstrip("/") + "/admin/replay",
        headers={"x-doorstep-passcode": passcode, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "telegram": args.telegram,
            "auto_approve": False,
            "timeout_seconds": 60,
            "decision_ttl_minutes": 30,
            "voice_residents": residents,
        },
        timeout=40,
    )
    body = reply.json()
    if reply.status_code != 202:
        print(f"refused: HTTP {reply.status_code}: {body}")
        return 1
    incident_id = body["incident_id"]
    print(f"incident {incident_id} (telegram={args.telegram}); waiting for its cases…")
    b = backend(aws, out)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            cases = b.for_incident(incident_id).cases(incident_id)
            if cases:
                states = sorted({str(c.state) for c in cases})
                print(f"{len(cases)} cases ready: {states}")
                break
        except NotFound:
            pass
        time.sleep(3)
    else:
        print("no cases after 120 s (did the profile activate?)")
        return 1
    print(f"\nincident id: {incident_id}")
    print(
        f"page:        http://localhost:{PAGE_PORT}/?incident={incident_id}&resident={residents[0]}"
    )
    return 0


# --- page --------------------------------------------------------------------------------------


def cmd_page(args: argparse.Namespace) -> int:
    out = outputs(session())
    (PAGE_DIR / "config.js").write_text(
        f"export default {json.dumps({'apiUrl': out['ApiUrl']})};\n", encoding="utf-8"
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".js": "text/javascript",
        }

    handler = functools.partial(Handler, directory=str(PAGE_DIR))
    server = http.server.ThreadingHTTPServer(("localhost", PAGE_PORT), handler)
    print(f"serving {PAGE_DIR.relative_to(ROOT)} on http://localhost:{PAGE_PORT} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


# --- e2e ---------------------------------------------------------------------------------------


def synth(text: str, workdir: Path, name: str, rate: int = 16000) -> bytes:
    aiff, wav = workdir / f"{name}.aiff", workdir / f"{name}.wav"
    subprocess.run(["say", "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{rate}", "-c", "1", str(aiff), str(wav)],
        check=True,
    )
    with wave.open(str(wav), "rb") as w:
        return w.readframes(w.getnframes())


async def run_e2e(args: argparse.Namespace) -> int:
    aws = session()
    out = outputs(aws)
    lines = SCRIPTS[args.script]
    with tempfile.TemporaryDirectory() as tmp:
        audio = [synth(t, Path(tmp), f"l{i}") for i, t in enumerate(lines)]

    t0 = time.monotonic()
    reply = httpx.post(
        out["ApiUrl"].rstrip("/") + "/voice/session",
        json={"incident_id": args.incident, "resident_id": args.resident},
        headers={"origin": f"http://localhost:{PAGE_PORT}"},
        timeout=20,
    )
    if reply.status_code != 201:
        print(f"link refused: HTTP {reply.status_code}: {reply.text}")
        return 1
    link = reply.json()
    print(f"[{time.monotonic() - t0:5.1f}s] link issued (expires in {link['expires_in']}s)")

    b = backend(aws, out)
    marks: dict[str, float] = {}
    pending: deque[bytes] = deque()
    play_until = 0.0
    agent_spoke = False
    idx = 0

    async with websockets.connect(link["url"], max_size=2**20, open_timeout=30) as ws:
        print(f"[{time.monotonic() - t0:5.1f}s] websocket open")

        async def feed() -> None:
            silence = bytes(640)
            next_at = time.monotonic()
            while True:
                chunk = pending.popleft() if pending else silence
                await ws.send(chunk)
                next_at += 0.02
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))

        async def turns() -> None:
            nonlocal idx, agent_spoke
            while True:
                await asyncio.sleep(0.1)
                if agent_spoke and not pending and time.monotonic() > play_until + 1.2:
                    if idx < len(lines):
                        print(f"[{time.monotonic() - t0:5.1f}s] resident says: {lines[idx]!r}")
                        pcm = audio[idx]
                        for i in range(0, len(pcm), 640):
                            pending.append(pcm[i : i + 640].ljust(640, b"\0"))
                        idx += 1
                        agent_spoke = False

        async def watch_page() -> None:
            """For the urgent script: when does a decision about this resident exist?"""
            while "page_seen" not in marks:
                await asyncio.sleep(0.5)
                decisions = await asyncio.to_thread(
                    lambda: [
                        d
                        for d in b.for_incident(args.incident).decisions(args.incident)
                        if d.resident_id == args.resident and d.status != "draft"
                    ]
                )
                if decisions:
                    marks["page_seen"] = time.monotonic() - t0
                    print(f"[{marks['page_seen']:5.1f}s] PAGE: decision {decisions[0].id} is out")

        tasks = [asyncio.create_task(feed()), asyncio.create_task(turns())]
        if args.script == "urgent":
            tasks.append(asyncio.create_task(watch_page()))
        try:
            async for message in ws:
                now = time.monotonic()
                if isinstance(message, bytes):
                    play_until = max(play_until, now) + len(message) / 32000
                    agent_spoke = True
                    continue
                event = json.loads(message)
                if event["type"] == "transcript":
                    print(f"[{now - t0:5.1f}s] {event['speaker']}: {event['text']}")
                elif event["type"] == "clear":
                    play_until = now
                    print(f"[{now - t0:5.1f}s] clear (barge-in)")
                elif event["type"] in ("ended", "error", "status"):
                    print(f"[{now - t0:5.1f}s] {event['type']}: {event}")
                    if event["type"] in ("ended", "error"):
                        marks["ended"] = now - t0
                        break
        finally:
            for t in tasks:
                t.cancel()
    hung_up = time.monotonic()
    marks.setdefault("ended", hung_up - t0)

    # Gate 4a: the result shows in the incident within 5 s of hang-up.
    result_at = None
    while time.monotonic() - hung_up < 30:
        case = await asyncio.to_thread(
            lambda: b.for_incident(args.incident).case(args.incident, args.resident)
        )
        attempt = case.attempt_log[-1] if case.attempt_log else None
        if attempt and attempt.transcript and attempt.result and attempt.meta.get("end_reason"):
            result_at = time.monotonic() - hung_up
            break
        await asyncio.sleep(0.25)
    if result_at is None:
        print("FAIL: no result in the incident 30 s after hang-up")
        return 1
    print(
        f"\nresult {attempt.result.status} for {args.resident} in the incident "
        f"{result_at:.1f}s after hang-up; case {case.state}; end {attempt.meta.get('end_reason')}"
    )
    ok = result_at <= 5.0
    if args.script == "urgent":
        before = marks.get("page_seen") is not None and marks["page_seen"] < marks["ended"]
        print(
            f"page before hang-up: {before} "
            f"(page seen at {marks.get('page_seen')}, ended at {marks['ended']:.1f})"
        )
        ok = ok and before and attempt.result.status == "URGENT"
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


# --- evidence ----------------------------------------------------------------------------------


def sonic_cost(usage: dict[str, Any]) -> float | None:
    total = usage.get("nova") or {}
    if not total:
        return None
    i, o = total.get("input", {}), total.get("output", {})
    return (
        i.get("speechTokens", 0) * SONIC_PRICE["speech_in"]
        + o.get("speechTokens", 0) * SONIC_PRICE["speech_out"]
        + i.get("textTokens", 0) * SONIC_PRICE["text_in"]
        + o.get("textTokens", 0) * SONIC_PRICE["text_out"]
    ) / 1e6


def cmd_evidence(args: argparse.Namespace) -> int:
    aws = session()
    b = backend(aws, outputs(aws))
    store = b.for_incident(args.incident)
    events = store.events(args.incident)
    for case in store.cases(args.incident):
        voice = [a for a in case.attempt_log if a.channel in ("browser", "phone")]
        for a in voice:
            meta = a.meta
            ended = datetime.fromisoformat(meta["ended_at"]) if meta.get("ended_at") else None
            applied = [
                e.at
                for e in events
                if e.resident_id in (None, case.resident_id)
                and e.data.get("event") == "checkin_attempt"
                and e.reason.startswith(f"{case.resident_id}:")
            ]
            latency = (applied[-1] - ended).total_seconds() if ended and applied else None
            paged = [
                e.at
                for e in events
                if e.resident_id == case.resident_id and "paging the captain" in e.reason
            ]
            sent = [
                d.delivery[0].sent_at
                for d in store.decisions(args.incident)
                if d.resident_id == case.resident_id and d.delivery
            ]  # noqa: E501
            cost = sonic_cost(meta.get("usage") or {})
            print(f"{case.resident_id} attempt {a.attempt} [{a.channel}] key {a.key[:6]}…")
            print(
                f"  started {a.started_at:%H:%M:%S}  hung up {ended:%H:%M:%S}"
                if ended
                else "  (no end)"
            )
            print(f"  end reason {meta.get('end_reason')}  barge-ins {meta.get('interruptions')}")
            print(f"  result {a.result.status if a.result else None}  case now {case.state}")
            print(f"  answers {a.answers}")
            print(
                f"  hang-up -> result applied: {latency:.1f}s"
                if latency is not None
                else "  result not applied"
            )
            if paged and ended:
                print(f"  captain paged {(ended - paged[0]).total_seconds():.1f}s BEFORE hang-up")
            if sent and ended:
                print(
                    "  decision delivered "
                    f"{(ended - sent[0]).total_seconds():.1f}s before hang-up (Telegram)"
                )
            print(
                f"  page source {meta.get('urgent_source') or '-'}; "
                f"delivered before hang-up (voice side): {meta.get('page_delivered')}"
            )
            print(
                f"  usage {json.dumps(meta.get('usage'))}  Sonic ≈ ${cost:.4f}"
                if cost is not None
                else f"  usage {meta.get('usage')}"
            )
            for t in a.transcript:
                print(f"    {t.speaker}: {t.text}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("incident")
    p.add_argument("--telegram", action="store_true")
    p.add_argument("--residents", default="")
    sub.add_parser("page")
    p = sub.add_parser("e2e")
    p.add_argument("--incident", required=True)
    p.add_argument("--resident", default="r04")
    p.add_argument("--script", choices=list(SCRIPTS), default="ok")
    p = sub.add_parser("evidence")
    p.add_argument("incident")
    args = ap.parse_args()
    if args.cmd == "incident":
        return cmd_incident(args)
    if args.cmd == "page":
        return cmd_page(args)
    if args.cmd == "e2e":
        return asyncio.run(run_e2e(args))
    return cmd_evidence(args)


if __name__ == "__main__":
    raise SystemExit(main())
