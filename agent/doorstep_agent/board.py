"""Plain-text terminal board for local drills (PLAN Phase 1 task 9)."""

from __future__ import annotations

import sys

from .models import CaseState, ResidentCase
from .runtime import RunContext
from .violations import find_violations

_CLEAR = "\x1b[2J\x1b[H"

STATE_ICON = {
    CaseState.QUEUED: "·",
    CaseState.CALLING: "☎",
    CaseState.OK: "✓",
    CaseState.NEEDS_HELP: "!",
    CaseState.URGENT: "‼",
    CaseState.NO_ANSWER: "?",
    CaseState.UNCLEAR: "?",
    CaseState.ASSIGNED: "→",
    CaseState.ESCALATED: "⚑",
    CaseState.RESOLVED: "✔",
}


def _result_label(case: ResidentCase) -> str:
    r = case.latest_result
    if r is None:
        return ""
    bits = [str(r.status)]
    if r.needs:
        bits.append("needs " + ",".join(r.needs))
    if r.red_flags:
        bits.append("flags " + ",".join(r.red_flags))
    if r.backstop and r.backstop.raised:
        bits.append("BACKSTOP↑")
    return " ".join(bits)


def decision_timeline(ctx: RunContext) -> list[str]:
    """Who decided what, and when — the Gate 2 evidence line, in time order.

    Read from the stored decisions rather than the audit log so it says what is true now: the
    responder, the option they picked, the moment they picked it, and how it reached them.
    """
    rows = []
    for d in sorted(
        ctx.store.decisions(ctx.incident_id),
        key=lambda d: d.responded_at or d.expires_at or d.created_at,
    ):
        if d.status == "draft":
            continue
        when = (d.responded_at or d.expires_at or d.created_at).strftime("%H:%M:%S")
        via = d.delivery[0].channel if d.delivery else "board"
        if d.status == "answered":
            chosen = d.option(d.response or "")
            what = f'chose "{chosen.label if chosen else d.response}"'
            who = d.responder or "?"
        elif d.status == "expired":
            what, who = "expired unanswered; nothing was sent", d.audience
        else:
            what, who = f"waiting ({', '.join(o.label for o in d.options)})", d.audience
        rows.append(f"{when}  {d.id:8} {d.resident_id or '-':4} {who:22} {what}  [via {via}]")
    return rows


def render(ctx: RunContext, *, wall_seconds: float, tail: int = 8) -> str:
    incident = ctx.store.incident(ctx.incident_id)
    cases = sorted(ctx.store.cases(ctx.incident_id), key=lambda c: (c.risk.wave, -c.risk.points))
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.state] = counts.get(c.state, 0) + 1
    denials = ctx.audit.denials()
    violations = find_violations(ctx)
    pending = ctx.store.decisions(ctx.incident_id, status="pending")

    lines = [
        f"DOORSTEP drill {incident.id} | profile {ctx.profile.display_name} | mode {ctx.mode} | "
        f"{wall_seconds:5.0f}s elapsed",
        f"Alert: {incident.alert.event} ({incident.alert.severity}) {incident.alert.vtec or ''}"
        f" | incident {incident.status}",
        "data: fictional roster and personas (data/roster.json, evals/personas); "
        "real archived NWS alert text",
        "",
        f"{'id':4} {'wave':4} {'pts':3} {'name':18} {'state':11} {'att':3} result",
        "-" * 100,
    ]
    for c in cases:
        r = ctx.store.resident(c.resident_id)
        extra = _result_label(c)
        if c.assigned_volunteer:
            extra += f" | {c.assigned_volunteer}"
        lines.append(
            f"{c.resident_id:4} {c.risk.wave:^4} {c.risk.points:3} {r.first_name[:18]:18} "
            f"{STATE_ICON[c.state]} {c.state:9} {c.attempts:3} {extra}"
        )
    summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: kv[0]))
    lines += [
        "-" * 100,
        f"cases: {summary}",
        f"policy: {len(denials)} denials, {len(violations)} violations | "
        f"decisions pending: {len(pending)} | messages recorded: {len(ctx.outbox)}",
    ]
    if pending:
        lines.append("WAITING ON A HUMAN:")
        for d in pending:
            options = " / ".join(o.label for o in d.options)
            sent = "sent" if d.delivery else "not sent"
            lines.append(
                f"  {d.id} [{d.name}] {d.resident_id or ''} -> {d.audience} ({sent}): "
                f"{d.reason[:60]} -> {options}"
            )
    answered = [
        d for d in ctx.store.decisions(ctx.incident_id) if d.status in ("answered", "expired")
    ]
    if answered:
        lines.append("HUMAN DECISIONS:")
        lines += [f"  {line}" for line in decision_timeline(ctx)]
    events = ctx.audit.events()[-tail:] if tail > 0 else []
    if events:
        lines.append("recent audit:")
        lines += [f"  {e.line()[:118]}" for e in events]
    return "\n".join(lines)


def show(text: str, *, clear: bool = True) -> None:
    if clear:
        sys.stdout.write(_CLEAR)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
