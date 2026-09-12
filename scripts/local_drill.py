"""Run a Doorstep drill locally with simulated residents (make local-drill).

Replays the June 2021 Portland Excessive Heat Warning for the 12 drill residents, shows a live
terminal board, and prints the Gate 1 evidence at the end. Exit codes: 0 when the gate criteria
pass (all cases settled, both urgent personas escalated, zero policy violations, under 4 min),
1 when they fail, 2 when the active profile did not activate on the alert (nothing to gate).

Options:
  --auto-approve      a simulated captain takes the first option of every pending decision
  --telegram          send decisions and tasks to the real roster chats and wait for real taps.
                      Off by default: a drill must not reach anyone's phone unless asked.
  --profile heat      hazard profile id
  --alert PATH        alert fixture (NWS alerts-API shape)
  --concurrency N     simulated check-ins in flight at once (default from settings)
  --compression F     time compression (default 30: 10 incident minutes = 20 s). Agent timers
                      only; a human's deadline to answer is never compressed.
  --decision-ttl M    real minutes a decision stays answerable (default 15). Use a small value
                      such as 0.5 to demonstrate a decision expiring.
  --timeout S         stop scheduling after S seconds (default 240)
  --no-board          only print the final board and report
  --no-clear          do not clear the screen between board refreshes
  --report PATH       write the JSON report here
  --transcripts       print every check-in transcript at the end
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from doorstep_agent import board
from doorstep_agent.drill import DrillRunner


def build_telegram():
    """A real Telegram channel, or a clear error about what is missing from .env.

    The volunteer chat ids are optional: one phone can play both parts by pointing
    TELEGRAM_VOLUNTEER_CHAT_IDS at the captain's own chat, which is what a solo run does.
    """
    import os

    from doorstep_agent.notify import Bot, TelegramNotifier

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    captain = os.getenv("TELEGRAM_CAPTAIN_CHAT_ID", "").strip()
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CAPTAIN_CHAT_ID", captain))
        if not value
    ]
    if missing:
        raise SystemExit(f"--telegram needs {' and '.join(missing)} in .env")
    if not os.getenv("TELEGRAM_VOLUNTEER_CHAT_IDS", "").strip():
        print(
            "note: TELEGRAM_VOLUNTEER_CHAT_IDS is empty, so volunteer tasks cannot be delivered "
            "and will be recorded instead. Set it to your own chat id to play both parts."
        )
    notifier = TelegramNotifier(Bot(token))
    return notifier, notifier.poll_once


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--telegram", action="store_true", help="use the real Telegram bot")
    ap.add_argument("--profile", help="hazard profile id (default: DOORSTEP_PROFILE or heat)")
    ap.add_argument("--alert", type=Path)
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--compression", type=float)
    ap.add_argument("--decision-ttl", type=float, help="real minutes before a decision expires")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--no-board", action="store_true")
    ap.add_argument("--no-clear", action="store_true")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--transcripts", action="store_true")
    args = ap.parse_args()

    notifier = taps = None
    if args.telegram:
        notifier, taps = build_telegram()
        if args.auto_approve:
            print(
                "--telegram with --auto-approve: the simulated captain answers first; "
                "drop --auto-approve to decide on your phone."
            )

    runner = DrillRunner(
        profile_id=args.profile,
        alert_path=args.alert,
        auto_approve=args.auto_approve,
        concurrency=args.concurrency,
        compression=args.compression,
        timeout_seconds=args.timeout,
        show_board=not args.no_board,
        clear_screen=not args.no_clear,
        notifier=notifier,
        notifier_taps=taps,
        decision_ttl_minutes=args.decision_ttl,
    )
    report = asyncio.run(runner.run())
    ctx = runner.ctx
    assert ctx is not None

    incident = ctx.store.incident(ctx.incident_id)
    print()
    print(board.render(ctx, wall_seconds=report.wall_seconds, tail=0))
    print()
    if incident.status != "active":
        rationale = incident.assessment.rationale if incident.assessment else "no assessment"
        print(f"Incident {incident.status} for profile '{ctx.profile.id}': {rationale}")
        print("No check-ins ran, so the Gate 1 criteria do not apply to this run.")
        print("RESULT: NOT ACTIVATED")
        return 2
    if not runner.personas:
        print(f"Profile '{ctx.profile.id}' has no persona set, so no check-ins were simulated.")
    if args.transcripts:
        for case in sorted(ctx.store.cases(ctx.incident_id), key=lambda c: c.resident_id):
            for attempt in case.attempt_log:
                print(f"--- {case.resident_id} attempt {attempt.attempt} ---")
                for turn in attempt.transcript:
                    print(f"  {turn.speaker:8}: {turn.text}")
        print()
    print("MESSAGES THAT WOULD HAVE BEEN SENT (drill: recorded only):")
    for m in ctx.outbox:
        print(f"  [{m.kind}] to {m.recipient}: {m.text[:140]}")
    print()
    print("WHO DECIDED WHAT, AND WHEN:")
    for line in board.decision_timeline(ctx):
        print(f"  {line}")
    if not ctx.store.decisions(ctx.incident_id):
        print("  no human decisions were needed")
    if runner.taps:
        print()
        print("TAPS RECEIVED:")
        for line in runner.taps:
            print(f"  {line}")
    print()
    print("POLICY DENIALS (attempts refused by Cedar or by a code check; the policy held):")
    for e in ctx.audit.denials():
        print(f"  {e.line()}")
    if not ctx.audit.denials():
        print("  none")
    print()
    print("POLICY VIOLATIONS (forbidden actions that actually happened):")
    for v in report.violations:
        print(f"  {v}")
    if not report.violations:
        print("  none")
    print()
    print("CASE OUTCOMES (expected -> result -> state):")
    for c in report.cases:
        print(
            f"  {c['resident_id']} wave {c['wave']} pts {c['points']:2}: {c['expected']:10} -> "
            f"{str(c['result']):10} -> {c['state']:9} attempts {c['attempts']} "
            f"{'BACKSTOP↑ ' if c['backstop_raised'] else ''}{c['outcome'] or ''}"
        )
    print()
    print("GATE 1:")
    print(f"  all 12 cases settled:        {report.all_settled} (unsettled: {report.unsettled})")
    print(
        f"  urgent personas escalated:   {report.urgent_escalated} of {report.urgent_expected}"
        f" -> {report.both_urgent_escalated}"
    )
    print(
        f"  policy violations:           {report.policy_violations} "
        f"(attempts denied and audited: {report.policy_denials})"
    )
    print(f"  audit events:                {report.audit_events}")
    print(f"  wall-clock:                  {report.wall_seconds}s (limit 240s)")
    passed = report.gate_passed()
    print(f"  RESULT:                      {'PASS' if passed else 'FAIL'}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report.to_json())
        print(f"  report written to {args.report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
