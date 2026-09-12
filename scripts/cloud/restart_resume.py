"""Gate 3: the Gate 2 restart test, against the deployed stack (make cloud-restart-test).

1. Seed a test incident (drill mode, no real channel: nobody's phone is messaged).
2. Send the coordinator a real `checkin_result` event: r01 is URGENT. Nova 2 Lite runs the
   dispatcher, which calls `escalate_to_captain` and pauses. The decision is `pending`, its
   session snapshot is in S3, and the audit line records process A's boot id.
3. `StopRuntimeSession`: the process that paused is terminated, not left idle.
4. Wait `--delay` seconds.
5. POST a Telegram-shaped tap ("Send <volunteer>") to the real `/telegram/webhook`, with the real
   secret header and the captain's chat id. A new microVM resumes the dispatcher from S3.
6. Exactly one volunteer task exists, the case is ASSIGNED, and the resume ran on boot id B != A.
7. The identical update twice more: no effect. A new tap on the same button: `already_answered`.
   A wrong secret: 401 and nothing.

Costs about one cent (two short dispatcher runs on Nova 2 Lite). Secrets are read into memory
from SSM and never printed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from doorstep_agent.cloud.coordinator import runtime_session_id  # noqa: E402
from doorstep_agent.models import Alert, Incident, ResidentCase  # noqa: E402
from doorstep_agent.profiles import load_profile  # noqa: E402
from doorstep_agent.risk import score_all  # noqa: E402
from doorstep_agent.sessions import session_id  # noqa: E402
from doorstep_agent.store_dynamo import DynamoBackend  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402

RESIDENT = "r01"
checks: list[tuple[str, bool]] = []


def check(label: str, ok: bool) -> bool:
    checks.append((label, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def wait_for(what: str, probe, timeout: float, every: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(every)
    print(f"  timed out after {timeout:.0f}s waiting for {what}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--delay", type=float, default=120.0, help="seconds between stop and tap")
    args = ap.parse_args()

    aws = session()
    out = outputs(aws)
    ssm = aws.client("ssm")
    agentcore = aws.client("bedrock-agentcore")

    def param(name: str) -> str:
        return ssm.get_parameter(Name=f"/doorstep/{name}", WithDecryption=True)["Parameter"][
            "Value"
        ]

    incident_id = f"cloudtest-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    sid = runtime_session_id(incident_id)
    subset = json.loads((ROOT / "data" / "roster.json").read_text())["drill_subset"]
    writer = DynamoBackend(out["TableName"], "juniper-court", client=aws.client("dynamodb"))
    store = writer.for_incident(incident_id, subset)

    def reader():
        return DynamoBackend(
            out["TableName"], "juniper-court", client=aws.client("dynamodb"), cache=False
        ).for_incident(incident_id, subset)

    print(f"1. seeding {incident_id} (session {sid})")
    profile = load_profile("heat")
    alert = Alert.from_fixture(
        json.loads((ROOT / "data/alerts/2021-06-pqr-excessive-heat-warning.json").read_text())
    )
    store.save_incident(
        Incident(
            id=incident_id,
            org_id="juniper-court",
            mode="drill",
            profile_id=profile.id,
            alert=alert,
            status="active",
            resident_ids=subset,
            run_options={"telegram": False, "compression": 30, "decision_ttl_minutes": 30},
        )
    )
    for rid, score in score_all(store.residents(), profile).items():
        store.save_case(ResidentCase(incident_id=incident_id, resident_id=rid, risk=score))

    def invoke(event: dict) -> dict:
        reply = agentcore.invoke_agent_runtime(
            agentRuntimeArn=out["RuntimeArn"],
            runtimeSessionId=sid,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"incident_id": incident_id, "event": event}).encode(),
        )
        return json.loads(reply["response"].read() or b"{}")

    print("2. check-in event -> dispatcher pauses (process A)")
    started = time.monotonic()
    first = invoke(
        {
            "type": "checkin_result",
            "resident_id": RESIDENT,
            "attempt_key": uuid.uuid4().hex,
            "result": {
                "status": "URGENT",
                "red_flags": ["confusion", "dizziness_fainting"],
                "key_quote": "I'm dizzy and I don't know what day it is.",
                "summary": "Dizzy and confused; told to call 911.",
                "confidence": 0.9,
            },
        }
    )
    boot_a = first.get("boot_id")
    check(
        f"coordinator accepted (boot {boot_a}, {time.monotonic() - started:.1f}s with cold start)",
        bool(first.get("accepted")),
    )
    pending = wait_for(
        "a pending decision",
        lambda: [
            d for d in reader().decisions(incident_id, "pending") if d.resident_id == RESIDENT
        ],
        timeout=180,
    )
    if not check("dispatcher paused with a pending captain decision", bool(pending)):
        return summary()
    decision = pending[0]
    check(
        f"{decision.id} [{decision.name}] carries interrupt and session ids",
        bool(decision.interrupt_id and decision.session_id),
    )

    s3 = aws.client("s3")
    prefix = f"sessions/session/{session_id(incident_id, RESIDENT)}/"
    keys = [
        o["Key"]
        for o in s3.list_objects_v2(Bucket=out["DataBucket"], Prefix=prefix).get("Contents", [])
    ]
    latest = [k for k in keys if k.endswith("snapshot_latest.json")]
    body = (
        s3.get_object(Bucket=out["DataBucket"], Key=latest[0])["Body"].read().decode()
        if latest
        else ""
    )
    check("paused agent snapshot is in S3 with the interrupt", decision.name in body)

    print("3. StopRuntimeSession: process A is terminated")
    try:
        agentcore.stop_runtime_session(
            agentRuntimeArn=out["RuntimeArn"], runtimeSessionId=sid, qualifier="DEFAULT"
        )
        check("session stopped", True)
    except agentcore.exceptions.ResourceNotFoundException:
        check("session already stopped", True)

    print(f"4. waiting {args.delay:.0f}s")
    time.sleep(args.delay)

    option = next((o for o in decision.options if o.action == "assign_volunteer"), None)
    if not check("the escalation offers a volunteer", option is not None):
        return summary()
    secret = param("telegram/webhook_secret")
    captain_chat = int(param("telegram/captain_chat_id"))
    url = out["ApiUrl"].rstrip("/") + "/telegram/webhook"
    update_id = random.randint(10**8, 10**9)

    def tap(uid: int, token: str = secret) -> int:
        update = {
            "update_id": uid,
            "callback_query": {
                "id": f"cloudtest-{uid}",
                "from": {"id": captain_chat},
                "message": {"message_id": 1, "chat": {"id": captain_chat}},
                "data": f"d|{incident_id}|{decision.id}|{option.id}",
            },
        }
        return httpx.post(
            url, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": token}, timeout=30
        ).status_code

    print(f'5. captain taps "{option.label}" through the real webhook')
    check("webhook accepted the tap", tap(update_id) == 200)

    def resumed():
        r = reader()
        d = r.decision(decision.id)
        tasks = [
            m
            for m in r.messages(incident_id)
            if m.kind == "volunteer_task" and m.resident_id == RESIDENT
        ]
        return (d, tasks, r) if d.status == "answered" and d.applied_at and tasks else None

    got = wait_for("the resume", resumed, timeout=240)
    if not check("decision answered and carried out", bool(got)):
        return summary()
    answered, tasks, r = got
    print("6. what happened")
    check(f"answered by {answered.responder}", answered.responder == "captain:cap-maria")
    check(f"exactly one volunteer task (to {tasks[0].recipient})", len(tasks) == 1)
    check(
        f"case is {r.case(incident_id, RESIDENT).state}",
        r.case(incident_id, RESIDENT).assigned_volunteer == tasks[0].recipient,
    )
    boots = [
        e.data.get("boot_id")
        for e in r.events(incident_id)
        if e.data.get("event") == "decision_response"
    ]
    check(
        f"resume ran in a different process (A={boot_a}, B={boots[0] if boots else None})",
        bool(boots) and boots[0] != boot_a,
    )

    print("7. replays")
    check("identical update, twice more: 200", tap(update_id) == 200 and tap(update_id) == 200)
    check("a new tap on the same button: 200", tap(update_id + 1) == 200)
    outcome = wait_for(
        "the second tap's outcome",
        lambda: [
            e for e in reader().events(incident_id) if e.data.get("outcome") == "already_answered"
        ],
        timeout=120,
    )
    check("the second tap was reported already_answered", bool(outcome))
    final = reader()
    tasks = [
        m
        for m in final.messages(incident_id)
        if m.kind == "volunteer_task" and m.resident_id == RESIDENT
    ]
    check("still exactly one volunteer task", len(tasks) == 1)
    responses = [e for e in final.events(incident_id) if e.data.get("event") == "decision_response"]
    check(f"coordinator saw 2 taps, not 4 ({len(responses)})", len(responses) == 2)
    check("a wrong secret is refused (401)", tap(update_id + 2, token="wrong") == 401)

    try:
        agentcore.stop_runtime_session(
            agentRuntimeArn=out["RuntimeArn"], runtimeSessionId=sid, qualifier="DEFAULT"
        )
    except Exception:  # noqa: BLE001 - already gone is fine
        pass
    return summary()


def summary() -> int:
    ok = all(passed for _, passed in checks)
    print(
        f"\nRESULT: {'PASS' if ok else 'FAIL'} ({sum(p for _, p in checks)}/{len(checks)} checks)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
