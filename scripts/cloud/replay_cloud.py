"""Gate 3: a 12-resident drill in AWS, started through the public API (make cloud-drill).

    make cloud-drill ARGS=--auto-approve   the coordinator's simulated captain answers (shakedown)
    make cloud-drill ARGS=--telegram       decisions go to the real roster chats; tap on your phone

POSTs /admin/replay with the captain passcode from `.env` and a fresh Idempotency-Key, then
renders the same terminal board as `make local-drill` from DynamoDB until the drill settles, and
prints the Gate 3 criteria. Exit 0 only when they all hold.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

from doorstep_agent import board  # noqa: E402
from doorstep_agent.agents.persona import load_personas  # noqa: E402
from doorstep_agent.audit import AuditLog  # noqa: E402
from doorstep_agent.config import settings  # noqa: E402
from doorstep_agent.drill import build_report  # noqa: E402
from doorstep_agent.profiles import load_profile  # noqa: E402
from doorstep_agent.runtime import RunContext, StoreOutbox  # noqa: E402
from doorstep_agent.state_machine import CasePolicy, Clock, is_settled  # noqa: E402
from doorstep_agent.store import NotFound  # noqa: E402
from doorstep_agent.store_dynamo import DynamoBackend  # noqa: E402
from scripts.cloud.stack import outputs, session  # noqa: E402


def read_context(table: str, incident_id: str, dynamodb) -> RunContext | None:
    """A read-only view of the cloud incident, rebuilt from DynamoDB on every call."""
    backend = DynamoBackend(table, "juniper-court", client=dynamodb, cache=False)
    try:
        incident = backend.for_incident(incident_id).incident(incident_id)
    except NotFound:
        return None
    store = backend.for_incident(incident_id, incident.resident_ids or None)
    clock = Clock(compression=float(incident.run_options.get("compression", 30)))
    return RunContext(
        store=store,
        incident_id=incident_id,
        profile=load_profile(incident.profile_id),
        org=store.org(),
        mode=incident.mode,
        clock=clock,
        policy=CasePolicy(clock),
        audit=AuditLog(store, incident_id),
        settings=settings(),
        alert_severity=incident.alert.severity,
        outbox=StoreOutbox(store, incident_id),
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--decision-ttl", type=float, default=15.0)
    ap.add_argument("--timeout", type=float, default=None, help="drill loop timeout, seconds")
    ap.add_argument("--no-clear", action="store_true")
    ap.add_argument("--report", type=Path, default=ROOT / "evals" / "cloud_drill_report.json")
    args = ap.parse_args()
    if args.telegram == args.auto_approve:
        raise SystemExit(
            "choose one: --telegram (you answer) or --auto-approve (simulated captain)"
        )
    timeout = args.timeout or (900.0 if args.telegram else 300.0)

    aws = session()
    out = outputs(aws)
    passcode = (dotenv_values(ROOT / ".env").get("CAPTAIN_PASSCODE") or "").strip()
    if not passcode:
        raise SystemExit(
            "CAPTAIN_PASSCODE is empty in .env (run make secrets-push ARGS=--generate-missing)"
        )

    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    reply = httpx.post(
        out["ApiUrl"].rstrip("/") + "/admin/replay",
        headers={"x-doorstep-passcode": passcode, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "telegram": args.telegram,
            "auto_approve": args.auto_approve,
            "decision_ttl_minutes": args.decision_ttl,
            "timeout_seconds": timeout,
        },
        timeout=40,
    )
    body = reply.json()
    if reply.status_code != 202:
        print(f"replay refused: HTTP {reply.status_code}: {body}")
        return 1
    incident_id = body["incident_id"]
    took = time.monotonic() - t0
    print(f"drill {incident_id} accepted in {took:.1f}s (session {body['session_id']})")

    dynamodb = aws.client("dynamodb")
    personas = {p.resident_id: p for p in load_personas(ROOT / "evals" / "personas" / "heat")}
    ctx = None
    deadline = time.monotonic() + timeout + 120
    finished = False
    while time.monotonic() < deadline:
        ctx = read_context(out["TableName"], incident_id, dynamodb)
        if ctx is not None:
            board.show(
                board.render(ctx, wall_seconds=time.monotonic() - t0), clear=not args.no_clear
            )
            incident = ctx.store.incident(incident_id)
            finished = any(e.reason == "drill loop finished" for e in ctx.audit.events())
            if incident.status == "not_activated" or finished:
                break
        time.sleep(5)

    if ctx is None:
        print("the incident never appeared in DynamoDB")
        return 1
    wall = round(time.monotonic() - t0, 1)
    report = build_report(ctx, personas, activated=True, started_at=started_at, wall_seconds=wall)
    events = ctx.audit.events()
    via_webhook = [
        e
        for e in events
        if e.type == "decision" and e.data.get("source") == "telegram" and e.data.get("option_id")
    ]
    stuck = [d.id for d in ctx.store.decisions(incident_id, "answered") if d.applied_at is None]
    cases = ctx.store.cases(incident_id)
    print()
    print(board.render(ctx, wall_seconds=wall, tail=0))
    print("\nWHO DECIDED WHAT, AND WHEN:")
    for line in board.decision_timeline(ctx):
        print(f"  {line}")
    print("\nGATE 3 (cloud drill):")
    rows = [
        ("drill loop finished in the runtime", finished),
        (f"all {len(cases)} cases settled", bool(cases) and all(is_settled(c) for c in cases)),
        (
            f"urgent personas escalated {report.urgent_escalated} of {report.urgent_expected}",
            report.both_urgent_escalated,
        ),
        (
            f"policy violations: {report.policy_violations} "
            f"(denials audited: {report.policy_denials})",
            report.policy_violations == 0,
        ),
        ("no decision answered but left unapplied", not stuck),
    ]
    if args.telegram:
        rows.append(
            (
                f"decisions answered on a phone through the webhook: {len(via_webhook)}",
                bool(via_webhook),
            )
        )
    for label, ok in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = all(ok for _, ok in rows)
    print(f"  wall-clock: {wall}s | audit events: {len(events)} | messages: {report.messages}")
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.to_json())
    print(f"  report written to {args.report.relative_to(ROOT)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
