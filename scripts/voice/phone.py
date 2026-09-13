"""The phone path, operated from this machine (Phase 4, Gate 4b).

bridge      run the Twilio Media Streams bridge on :8765 behind ngrok, and publish its wss URL
            to SSM `/doorstep/voice_bridge_url` (the dialer reads it). Leave it running.
preflight   read-only checks before any real call: stack, parameters (names only), the
            operator number is allowlist[0], ngrok reaches the bridge, the Twilio credentials
            are a subaccount, the kill switch, and a deployed dialer refusing bad jobs
rehearse    the whole phone chain with **no phone ringing**: a live incident for a resident with
            no phone (Cedar denies the call in the cloud), then a synthetic Twilio client streams
            `say` audio as 8 kHz mu-law through ngrok into the bridge, Sonic and the coordinator
call        a real call: a live `operator_test` incident for r01 (the operator's own phone)
evidence    the incident's record of the call (same report as `make voice-evidence`)

make phone-bridge
make phone-preflight
make phone-rehearse ARGS="--script urgent"
make phone-call ARGS="--telegram"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
import subprocess
import sys
import tempfile
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

import httpx  # noqa: E402
import websockets  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from doorstep_agent.cloud.coordinator import runtime_session_id  # noqa: E402
from doorstep_agent.cloud.ssm import SsmFlags  # noqa: E402
from doorstep_agent.runtime import normalize_number  # noqa: E402
from doorstep_agent.store_dynamo import DynamoBackend  # noqa: E402
from doorstep_api.twilio_rest import TwilioRest  # noqa: E402
from doorstep_voice import tokens  # noqa: E402
from doorstep_voice.audio import pcm16_to_ulaw  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402
from scripts.voice.voice import SCRIPTS, cmd_evidence, synth  # noqa: E402

PORT = 8765
ENV = dotenv_values(ROOT / ".env")


def new_live_id() -> str:
    return f"live-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def invoke(aws: Any, out: dict[str, str], incident_id: str, event: dict[str, Any]) -> dict:
    result = aws.client("bedrock-agentcore").invoke_agent_runtime(
        agentRuntimeArn=out["RuntimeArn"],
        runtimeSessionId=runtime_session_id(incident_id),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"incident_id": incident_id, "event": event}).encode(),
    )
    body = result["response"]
    return json.loads((body.read() if hasattr(body, "read") else body) or b"{}")


# --- bridge -------------------------------------------------------------------------------------


def ngrok_url() -> str | None:
    try:
        tunnels = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2).json()["tunnels"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    for t in tunnels:
        if t.get("public_url", "").startswith("https://") and str(PORT) in t["config"]["addr"]:
            return t["public_url"]
    return None


def cmd_bridge(args: argparse.Namespace) -> int:
    import logging

    import uvicorn

    from doorstep_voice.phone import build_app
    from doorstep_voice.serve import VoiceDeps
    from doorstep_voice.sink import CoordinatorSink, coordinator_client

    aws = session()
    out = outputs(aws)
    proc = None
    if ngrok_url() is None:
        proc = subprocess.Popen(
            ["ngrok", "http", str(PORT), "--log", "stdout", "--log-format", "json"],
            stdout=subprocess.DEVNULL,
        )
        for _ in range(40):
            if ngrok_url():
                break
            time.sleep(0.5)
    public = ngrok_url()
    if not public:
        print("ngrok did not come up (run `ngrok config check`)")
        return 1
    wss = public.replace("https://", "wss://") + "/twilio"
    aws.client("ssm").put_parameter(
        Name="/doorstep/voice_bridge_url", Value=wss, Type="String", Overwrite=True
    )
    print(f"bridge: {wss} (published to /doorstep/voice_bridge_url)")

    def deps() -> VoiceDeps:
        backend = DynamoBackend(
            "doorstep", "juniper-court", client=aws.client("dynamodb"), cache=False
        )
        return VoiceDeps(
            backend=backend,
            sink=CoordinatorSink(
                runtime_arn=out["RuntimeArn"],
                backend=backend,
                client=coordinator_client(aws),
            ),
            flags=SsmFlags(client=aws.client("ssm")),
        )

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    for noisy in ("botocore", "httpx", "strands"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        uvicorn.run(build_app(deps), host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        if proc:
            proc.terminate()
    return 0


# --- preflight ----------------------------------------------------------------------------------


def cmd_preflight(args: argparse.Namespace) -> int:
    aws = session()
    out = outputs(aws)
    ssm = aws.client("ssm")
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}{': ' + detail if detail else ''}")

    names = [
        "call_allowlist", "operator_test_number", "twilio/subaccount_sid",
        "twilio/subaccount_token", "twilio/from_number", "voice_bridge_url",
        "internal_hmac_secret", "kill_switch",
    ]  # fmt: skip
    found = {
        p["Name"].rsplit("/doorstep/", 1)[1]: p["Value"]
        for i in range(0, len(names), 10)
        for p in ssm.get_parameters(
            Names=[f"/doorstep/{n}" for n in names[i : i + 10]], WithDecryption=True
        )["Parameters"]
    }
    check(
        "SSM parameters exist", set(names) <= set(found), ", ".join(sorted(set(names) - set(found)))
    )
    allow = [normalize_number(x) for x in found.get("call_allowlist", "").split(",")]
    operator = normalize_number(found.get("operator_test_number"))
    check(
        "operator number is allowlist[0] (resident r01)", bool(operator) and allow[:1] == [operator]
    )
    check(
        "kill switch is off", found.get("kill_switch", "").strip().lower() in ("off", "false", "0")
    )
    check(
        "the stack has a dialer",
        "doorstep-checkin-worker"
        in json.dumps(
            aws.client("lambda").get_function(FunctionName="doorstep-checkin-worker")[
                "Configuration"
            ]
        ),
    )
    bridge = found.get("voice_bridge_url", "")
    try:
        health = httpx.get(
            bridge.replace("wss://", "https://").replace("/twilio", "/healthz"), timeout=5
        )
        check("ngrok reaches the local bridge", health.status_code == 200, bridge)
    except httpx.HTTPError as exc:
        check(
            "ngrok reaches the local bridge",
            False,
            f"{type(exc).__name__} (is make phone-bridge running?)",
        )
    sid = found.get("twilio/subaccount_sid", "")
    account = TwilioRest(sid, found.get("twilio/subaccount_token", "")).account()
    check(
        "Twilio credentials are a subaccount (read-only lookup)",
        bool(account.get("owner_account_sid")) and account.get("owner_account_sid") != sid,
        f"status {account.get('status')}",
    )
    # The deployed dialer must refuse a drill incident and an unknown one (no phone rings).
    lam = aws.client("lambda")
    for label, job in (
        (
            "deployed dialer refuses an unknown incident",
            {"incident_id": "live-nope-0000", "resident_id": "r01", "attempt": 1},
        ),  # noqa: E501
        ("deployed dialer refuses a malformed job", {"incident_id": "../x", "resident_id": "r01"}),
    ):
        reply = lam.invoke(
            FunctionName="doorstep-checkin-worker",
            Payload=json.dumps({"Records": [{"body": json.dumps(job)}]}).encode(),
        )
        result = json.loads(reply["Payload"].read())
        check(
            label,
            result.get("results", [{}])[0].get("dialled") is False,
            result.get("results", [{}])[0].get("reason", ""),
        )
    status = invoke(aws, out, "live-nope-0000", {"type": "status"})
    check(
        "coordinator answers",
        "error" in status or status.get("ok"),
        str(status.get("error", ""))[:60],
    )
    print("PREFLIGHT PASS" if ok else "PREFLIGHT FAIL")
    return 0 if ok else 1


# --- rehearse ---------------------------------------------------------------------------------


async def synthetic_twilio_call(
    wss: str, token: str, sid: str, lines: list[str], b: DynamoBackend, incident: str, resident: str
) -> dict[str, float]:  # noqa: E501
    with tempfile.TemporaryDirectory() as tmp:
        audio = [
            pcm16_to_ulaw(synth(t, Path(tmp), f"l{i}", rate=8000)) for i, t in enumerate(lines)
        ]
    t0 = time.monotonic()
    marks: dict[str, float] = {}
    pending: deque[bytes] = deque()
    state = {"play_until": 0.0, "spoke": False, "idx": 0}
    stream = "MZ" + secrets.token_hex(16)
    async with websockets.connect(wss, open_timeout=20) as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await ws.send(json.dumps({"event": "start", "sequenceNumber": "1", "start": {
            "streamSid": stream, "callSid": "CA" + secrets.token_hex(16), "accountSid": sid,
            "tracks": ["inbound"], "customParameters": {"token": token},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        }, "streamSid": stream}))  # fmt: skip
        print(f"[{time.monotonic() - t0:5.1f}s] stream started")

        async def feed() -> None:
            silence = b"\xff" * 160
            next_at = time.monotonic()
            while True:
                chunk = pending.popleft() if pending else silence
                payload = base64.b64encode(chunk).decode()
                await ws.send(
                    json.dumps(
                        {"event": "media", "streamSid": stream, "media": {"payload": payload}}
                    )
                )  # noqa: E501
                next_at += 0.02
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))

        async def turns() -> None:
            while True:
                await asyncio.sleep(0.1)
                if state["spoke"] and not pending and time.monotonic() > state["play_until"] + 1.2:
                    if state["idx"] < len(lines):
                        print(
                            f"[{time.monotonic() - t0:5.1f}s] resident says: {lines[state['idx']]!r}"  # noqa: E501
                        )
                        ulaw = audio[state["idx"]]
                        for i in range(0, len(ulaw), 160):
                            pending.append(ulaw[i : i + 160].ljust(160, b"\xff"))
                        state["idx"] += 1
                        state["spoke"] = False

        async def watch_page() -> None:
            while "page" not in marks:
                await asyncio.sleep(0.5)
                found = await asyncio.to_thread(
                    lambda: [
                        d
                        for d in b.for_incident(incident).decisions(incident)
                        if d.resident_id == resident and d.status != "draft"
                    ]  # noqa: E501
                )
                if found:
                    marks["page"] = time.monotonic() - t0
                    print(f"[{marks['page']:5.1f}s] PAGE: {found[0].id} is out")

        tasks = [asyncio.create_task(x) for x in (feed(), turns(), watch_page())]
        try:
            async for raw in ws:
                now = time.monotonic()
                msg = json.loads(raw)
                if msg["event"] == "media":
                    n = len(base64.b64decode(msg["media"]["payload"]))
                    state["play_until"] = max(state["play_until"], now) + n / 8000
                    state["spoke"] = True
                elif msg["event"] == "clear":
                    state["play_until"] = now
                    print(f"[{now - t0:5.1f}s] clear (barge-in)")
                elif msg["event"] == "mark":
                    await asyncio.sleep(max(0.0, state["play_until"] - now))
                    await ws.send(
                        json.dumps({"event": "mark", "streamSid": stream, "mark": msg["mark"]})
                    )  # noqa: E501
        except websockets.ConnectionClosed:
            pass
        finally:
            for t in tasks:
                t.cancel()
    marks["ended"] = time.monotonic() - t0
    print(f"[{marks['ended']:5.1f}s] bridge closed the stream (Twilio would hang up)")
    return marks


def cmd_rehearse(args: argparse.Namespace) -> int:
    aws = session()
    out = outputs(aws)
    found = aws.client("ssm").get_parameters(
        Names=[
            "/doorstep/voice_bridge_url",
            "/doorstep/internal_hmac_secret",
            "/doorstep/twilio/subaccount_sid",
        ],
        WithDecryption=True,
    )["Parameters"]
    p = {x["Name"].rsplit("/", 1)[-1]: x["Value"] for x in found}
    incident = new_live_id()
    reply = invoke(
        aws,
        out,
        incident,
        {"type": "live_call", "resident_id": args.resident, "telegram": args.telegram},
    )  # noqa: E501
    print(f"live_call {incident} for {args.resident}: {reply}")
    if "DENIED" not in str(reply.get("outcome")) and "refused" not in str(reply.get("outcome")):
        print("STOP: the call was not refused; a rehearsal must never queue a real call")
        return 1
    token, _ = tokens.mint(
        p["internal_hmac_secret"],
        incident_id=incident,
        resident_id=args.resident,
        channel="phone",
        mode="live",
        ttl_seconds=60,
    )  # noqa: E501
    b = DynamoBackend("doorstep", "juniper-court", client=aws.client("dynamodb"), cache=False)
    wss = f"ws://127.0.0.1:{PORT}/twilio" if args.local else p["voice_bridge_url"]
    marks = asyncio.run(
        synthetic_twilio_call(
            wss,
            token,
            p["subaccount_sid"],
            SCRIPTS[args.script],
            b,
            incident,
            args.resident,
        )
    )  # noqa: E501
    time.sleep(4)
    case = b.for_incident(incident).case(incident, args.resident)
    attempt = case.attempt_log[-1] if case.attempt_log else None
    status = attempt.result.status if attempt and attempt.result else None
    print(f"\ncase {case.state}; result {status}; channel {attempt.channel if attempt else None}")
    good = attempt is not None and attempt.channel == "phone" and attempt.result is not None
    if args.script == "urgent":
        good = good and "page" in marks and marks["page"] < marks["ended"] and status == "URGENT"
        print(
            f"page before the stream closed: {'page' in marks and marks['page'] < marks['ended']}"
        )
    print("REHEARSAL PASS" if good else "REHEARSAL FAIL")
    print(f"evidence: make phone-evidence ARGS={incident}")
    return 0 if good else 1


# --- call -------------------------------------------------------------------------------------


def cmd_call(args: argparse.Namespace) -> int:
    aws = session()
    out = outputs(aws)
    incident = new_live_id()
    print(f"placing a real call: incident {incident}, resident r01 (the operator's phone)")
    reply = invoke(
        aws, out, incident,
        {"type": "live_call", "resident_id": "r01", "operator_test": True, "telegram": args.telegram},  # noqa: E501
    )  # fmt: skip
    print(f"coordinator: {reply}")
    print(f"evidence afterwards: make phone-evidence ARGS={incident}")
    return 0 if "queued" in str(reply.get("outcome")) else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bridge")
    sub.add_parser("preflight")
    r = sub.add_parser("rehearse")
    r.add_argument("--resident", default="r04", help="a resident with NO phone reference")
    r.add_argument("--script", choices=list(SCRIPTS), default="ok")
    r.add_argument("--telegram", action="store_true")
    r.add_argument(
        "--local", action="store_true", help="connect to the bridge on localhost, not ngrok"
    )
    c = sub.add_parser("call")
    c.add_argument("--telegram", action="store_true")
    e = sub.add_parser("evidence")
    e.add_argument("incident")
    args = ap.parse_args()
    return {
        "bridge": cmd_bridge,
        "preflight": cmd_preflight,
        "rehearse": cmd_rehearse,
        "call": cmd_call,
        "evidence": cmd_evidence,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
