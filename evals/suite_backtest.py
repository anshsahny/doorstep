"""Suite 4 (SPEC §12.4): the June 2021 replay backtest over all 48 residents.

The real NWS Portland Excessive Heat Warning of June 2021 goes through the whole local pipeline
(`DrillRunner`: assess -> triage -> outreach -> check-ins -> classifier -> dispatcher -> decisions)
with every resident simulated. A simulated captain takes each decision's first option and a
simulated volunteer reports back, so the run finishes unattended.

Two clocks, both reported, because they answer different questions:

* **measured**: this run's wall clock. Check-ins are text conversations with simulated residents
  and retries are compressed 30x, so this shows the agent pipeline's own speed, not a phone day.
* **projected**: the same run's actual attempts (who answered, who did not, who was urgent,
  in the order the call plan put them) replayed on phone lines at measured phone-call lengths
  (Gate 4b) with the profile's real 10-minute retry interval. Assumptions are in the output.
"""

from __future__ import annotations

from typing import Any

from doorstep_agent.drill import DrillRunner
from doorstep_agent.messages import VOLUNTEER_UPDATE
from doorstep_agent.models import CaseState

from . import scoring
from .common import BACKTEST_SET, DRILL_SET, EVAL_SET

SUITE = "backtest"

# Measured on real calls (PROGRESS Gates 4b and 5): full OK calls ran 66 s and 68 s; urgent
# calls ended 44-48 s after they began; a result was applied 1.1-2.5 s after hang-up. An
# unanswered attempt is an assumption: about six rings.
ANSWERED_S = 67.0
URGENT_S = 46.0
NO_ANSWER_S = 30.0
DECIDE_S = 2.5
PHONE_TREE_MIN_PER_RESIDENT = 4.0


def _kinds(case: Any) -> list[str]:
    out = []
    for a in case.attempt_log:
        if not a.answered:
            out.append("no_answer")
        elif a.result is not None and str(a.result.status) == "URGENT":
            out.append("urgent")
        else:
            out.append("answered")
    return out


async def run(label: str, *, lines: int = 6) -> dict[str, Any]:
    runner = DrillRunner(
        auto_approve=True,
        show_board=False,
        clear_screen=False,
        timeout_seconds=1500,
        persona_dirs=[DRILL_SET, EVAL_SET, BACKTEST_SET],
        incident_id=f"backtest-{label}",
    )
    report = await runner.run()
    ctx = runner.ctx
    assert ctx is not None
    events = ctx.audit.events()
    alert_at = next(e.at for e in events if e.type == "incident" and "alert received" in e.reason)
    cases = ctx.store.cases(ctx.incident_id)
    reached_at = {}
    first_call_at = None
    for c in cases:
        for t in c.history:
            if t.to_state == CaseState.CALLING:
                first_call_at = t.at if first_call_at is None else min(first_call_at, t.at)
            if (
                t.to_state
                in (CaseState.OK, CaseState.NEEDS_HELP, CaseState.URGENT, CaseState.ESCALATED)
                and c.resident_id not in reached_at
            ):
                reached_at[c.resident_id] = t.at
    decisions = ctx.store.decisions(ctx.incident_id)
    captain = [d for d in decisions if d.audience == ctx.org.captain_id]
    tasks = [d for d in decisions if d.name == VOLUNTEER_UPDATE]
    agent_actions = [
        e
        for e in events
        if e.type == "tool_call"
        and e.actor.startswith("agent:")
        and e.reason != "attempted"
        and e.tool
        not in (
            "get_resident_memory",
            "find_relief_centres",
            "find_nearest_volunteers",
            "record_answer",
        )
        and not (e.data or {}).get("cancelled")
    ]
    checkins = sum(len(c.attempt_log) for c in cases)
    order = {rid: i for i, rid in enumerate(ctx.extras.get("queued", []))}
    plan = [
        {"resident_id": c.resident_id, "attempts": _kinds(c)}
        for c in sorted(cases, key=lambda c: order.get(c.resident_id, 999))
        if c.attempt_log
    ]
    projection = scoring.project_timeline(
        plan,
        lines=lines,
        answered_seconds=ANSWERED_S,
        urgent_seconds=URGENT_S,
        no_answer_seconds=NO_ANSWER_S,
        retry_minutes=ctx.settings.retry_interval_minutes,
        decide_seconds=DECIDE_S,
    )
    one_line = scoring.project_timeline(
        plan,
        lines=1,
        answered_seconds=ANSWERED_S,
        urgent_seconds=URGENT_S,
        no_answer_seconds=NO_ANSWER_S,
        retry_minutes=ctx.settings.retry_interval_minutes,
        decide_seconds=DECIDE_S,
    )
    urgent_expected = [
        p.resident_id for p in runner.personas.values() if p.ground_truth.status == "URGENT"
    ]
    escalated = [
        c.resident_id for c in cases if any(t.to_state == CaseState.ESCALATED for t in c.history)
    ]
    return {
        "suite": SUITE,
        "label": label,
        "metrics": {
            "residents": len(cases),
            "activated": report.activated,
            "all_settled": report.all_settled,
            "unsettled": report.unsettled,
            "measured_alert_to_first_call_s": round((first_call_at - alert_at).total_seconds(), 1)
            if first_call_at
            else None,
            "measured_alert_to_all_reached_or_escalated_s": round(
                (max(reached_at.values()) - alert_at).total_seconds(), 1
            )
            if reached_at
            else None,
            "measured_reached_or_escalated": len(reached_at),
            "measured_wall_s": report.wall_seconds,
            "projected_lines": lines,
            "projected_all_reached_or_escalated_min": projection[
                "all_reached_or_escalated_minutes"
            ],
            "projected_one_line_min": one_line["all_reached_or_escalated_minutes"],
            "phone_tree_baseline_min": len(cases) * PHONE_TREE_MIN_PER_RESIDENT,
            "checkin_attempts": checkins,
            "captain_decisions": len(captain),
            "captain_decision_names": sorted({d.name for d in captain}),
            "volunteer_tasks": len(tasks),
            "automated_actions": len(agent_actions) + checkins,
            "automated_breakdown": {
                "check_in_attempts": checkins,
                "agent_tool_actions": len(agent_actions),
                "messages_recorded": len(ctx.outbox),
            },
            "urgent_expected": len(urgent_expected),
            "urgent_escalated": len([r for r in urgent_expected if r in escalated]),
            "urgent_missed": sorted(r for r in urgent_expected if r not in escalated),
            "policy_denials": report.policy_denials,
            "policy_violations": report.policy_violations,
            "violations": report.violations,
        },
        "assumptions": {
            "answered_call_s": ANSWERED_S,
            "urgent_call_s": URGENT_S,
            "unanswered_attempt_s": NO_ANSWER_S,
            "result_applied_s": DECIDE_S,
            "retry_minutes": ctx.settings.retry_interval_minutes,
            "lines": lines,
            "poller": "the NWS poller runs every 10 minutes, so detection can add up to 10 min",
            "simulated_captain": "takes each decision's first option; volunteers report OK",
        },
        "plan": plan,
        "decisions": [
            {
                "id": d.id,
                "name": d.name,
                "resident_id": d.resident_id,
                "status": d.status,
                "response": d.response,
            }
            for d in decisions
        ],
        "cases": report.cases,
    }
