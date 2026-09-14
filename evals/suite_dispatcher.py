"""Suite 2 (SPEC §12.2): dispatcher trajectories, 12 scenarios.

Each scenario hands the production dispatcher (Nova 2 Lite, Cedar, audit and approval hooks) one
check-in result, answers every decision it raises the way the scenario says, through the same
`respond_to_decision` a Telegram tap or a dashboard click uses, and scores the tool calls it made
in order. Scoring is deterministic: the expected orders are exact, so no LLM judge is needed.
"""

from __future__ import annotations

from typing import Any

from strands_evals import Case, Experiment, LocalFileTaskResultStore
from strands_evals.evaluators import Evaluator
from strands_evals.types import EvaluationData, EvaluationOutput

from doorstep_agent.agents.dispatcher import dispatch
from doorstep_agent.decisions import Responder, respond_to_decision
from doorstep_agent.messages import VOLUNTEER_UPDATE
from doorstep_agent.models import BackstopOutcome, CheckinResult, CheckinStatus
from doorstep_agent.violations import find_violations

from . import scoring
from .common import CACHE, eval_context

SUITE = "dispatcher"
NEVER = ["record_emergency_call", "broadcast_to_volunteers", "place_checkin_call"]

URGENT = "doorstep-urgent-red-flag"
NO_ANSWER = "doorstep-high-risk-no-answer"
UNMET = "doorstep-unmet-need"
DOOR_KNOCK = "doorstep-approve-door-knock"


def _result(status: str, **kw: Any) -> dict[str, Any]:
    return {"status": status, **kw}


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "s01_urgent_send_volunteer",
        "resident_id": "r01",
        "attempts": ["answered"],
        "result": _result(
            "URGENT",
            red_flags=["dizziness_fainting"],
            needs=["cooling"],
            key_quote="I'm dizzy and I've stopped sweating",
            summary="Resident is dizzy, hot and not sweating.",
        ),
        "captain": {URGENT: "send_volunteer"},
        "first_tool": "escalate_to_captain",
        "ignore_before_first": ["get_resident_memory", "find_nearest_volunteers"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "decision_name": URGENT,
        "final_state": "RESOLVED",
    },
    {
        "id": "s02_urgent_hidden_backstop_captain_handles",
        "resident_id": "r02",
        "attempts": ["answered"],
        "result": _result(
            "URGENT",
            red_flags=["confusion"],
            key_quote="just not sure what day it is",
            summary="Resident says they are fine.",
            backstop={
                "model_status": "OK",
                "final_status": "URGENT",
                "raised": True,
                "matched_categories": ["confusion"],
                "matched_phrases": ["not sure what day"],
                "disagreement": "backstop_raised",
            },
        ),
        "captain": {URGENT: "handle"},
        "first_tool": "escalate_to_captain",
        "ignore_before_first": ["get_resident_memory"],
        "forbidden": NEVER + ["assign_volunteer", "send_resident_tip"],
        "captain_decisions": 1,
        "decision_name": URGENT,
        "final_state": "RESOLVED",
    },
    {
        "id": "s03_urgent_with_needs_escalates_only",
        "resident_id": "r30",
        "attempts": ["answered"],
        "result": _result(
            "URGENT",
            red_flags=["chest_pain"],
            needs=["cooling"],
            key_quote="my chest hurts, a tight pressure",
            summary="Resident reports chest pain; no AC.",
        ),
        "captain": {URGENT: "handle"},
        "first_tool": "escalate_to_captain",
        "ignore_before_first": ["get_resident_memory"],
        "forbidden": NEVER + ["assign_volunteer", "send_resident_tip", "notify_family"],
        "captain_decisions": 1,
        "final_state": "RESOLVED",
    },
    {
        "id": "s04_needs_high_risk_door_knock_approved",
        "resident_id": "r03",
        "attempts": ["answered"],
        "result": _result(
            "NEEDS_HELP",
            needs=["water"],
            key_quote="se me está acabando el agua",
            summary="Resident is well but almost out of water.",
        ),
        "captain": {DOOR_KNOCK: "approve"},
        "in_order": ["find_nearest_volunteers", "assign_volunteer"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "decision_name": DOOR_KNOCK,
        "final_state": "RESOLVED",
    },
    {
        "id": "s05_needs_high_risk_captain_handles_it",
        "resident_id": "r04",
        "attempts": ["answered"],
        "result": _result(
            "NEEDS_HELP",
            needs=["ride"],
            key_quote="I could use a ride to somewhere cool",
            summary="Resident is fine but needs a ride to a cooling centre.",
        ),
        "captain": {DOOR_KNOCK: "handle", UNMET: "handle"},
        "in_order": ["assign_volunteer"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "final_state": "RESOLVED",
    },
    {
        "id": "s06_needs_low_risk_volunteer_no_approval",
        "resident_id": "r19",
        "attempts": ["answered"],
        "result": _result(
            "NEEDS_HELP",
            needs=["ride", "cooling"],
            key_quote="a ride to a cooling centre would really help",
            summary="Resident is fine; warm house, would like a ride.",
        ),
        "in_order": ["send_resident_tip", "assign_volunteer"],
        "forbidden": NEVER + ["escalate_to_captain"],
        "captain_decisions": 0,
        "final_state": "RESOLVED",
    },
    {
        "id": "s07_ok_tip_recheck_close",
        "resident_id": "r06",
        "attempts": ["answered"],
        "result": _result("OK", summary="Resident is fine and has what they need."),
        "in_order": ["send_resident_tip", "schedule_recheck", "close_case"],
        "forbidden": NEVER + ["escalate_to_captain", "assign_volunteer", "notify_family"],
        "captain_decisions": 0,
        "final_state": "RESOLVED",
    },
    {
        "id": "s08_ok_spanish",
        "resident_id": "r07",
        "attempts": ["answered"],
        "result": _result(
            "OK", summary="La residente está bien, con aire acondicionado y familia."
        ),
        "in_order": ["send_resident_tip", "schedule_recheck", "close_case"],
        "forbidden": NEVER + ["escalate_to_captain", "assign_volunteer", "notify_family"],
        "captain_decisions": 0,
        "final_state": "RESOLVED",
    },
    {
        "id": "s09_no_answer_three_times_family",
        "resident_id": "r05",
        "attempts": ["no_answer", "no_answer", "no_answer"],
        "result": _result("NO_ANSWER", summary="No answer."),
        "captain": {NO_ANSWER: "notify_family"},
        "first_tool": "escalate_to_captain",
        "ignore_before_first": ["get_resident_memory", "find_nearest_volunteers"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "decision_name": NO_ANSWER,
        "final_state": "RESOLVED",
    },
    {
        "id": "s10_unclear_three_times_not_an_unmet_need",
        "resident_id": "r11",
        "attempts": ["unclear", "unclear", "unclear"],
        "result": _result("UNCLEAR", summary="Call ended before the resident said how they are."),
        "captain": {NO_ANSWER: "handle", UNMET: "handle"},
        "first_tool": "escalate_to_captain",
        "ignore_before_first": ["get_resident_memory", "find_nearest_volunteers"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "decision_name_not": UNMET,
        "final_state": "RESOLVED",
    },
    {
        "id": "s11_needs_nobody_available_unmet_need",
        "resident_id": "r17",
        "attempts": ["answered"],
        "unavailable": "all",
        "result": _result(
            "NEEDS_HELP",
            needs=["cooling", "ride"],
            key_quote="the fan broke and it's stuffy up here",
            summary="Resident is fine for now; fan broke, top floor is hot.",
        ),
        "captain": {UNMET: "handle"},
        "in_order": ["escalate_to_captain"],
        "forbidden": NEVER,
        "captain_decisions": 1,
        "decision_name": UNMET,
        "final_state": "RESOLVED",
    },
    {
        "id": "s12_needs_company_no_sharing_consent",
        "resident_id": "r46",
        "attempts": ["answered"],
        "result": _result(
            "NEEDS_HELP",
            needs=["company"],
            key_quote="a visit would mean a lot",
            summary="Resident is fine but would like a visit.",
        ),
        "in_order": ["assign_volunteer"],
        "forbidden": NEVER + ["notify_family"],
        "captain_decisions": 0,
        "final_state": "RESOLVED",
    },
]


def cases(trials: int = 2, only: list[str] | None = None) -> list[Case]:
    return [
        Case(name=f"{s['id']}__t{t}", input={"scenario": s["id"], "trial": t}, expected_output=s)
        for s in SCENARIOS
        if not only or s["id"] in only
        for t in range(1, trials + 1)
    ]


def _snapshot(ctx: Any, rid: str) -> dict[str, Any]:
    case = ctx.store.case(ctx.incident_id, rid)
    pending = [
        d
        for d in ctx.store.decisions(ctx.incident_id)
        if d.resident_id == rid and d.status in ("draft", "pending")
    ]
    return {"state": str(case.state), "pending": len(pending)}


def _setup(ctx: Any, scenario: dict[str, Any]) -> tuple[Any, CheckinResult]:
    rid = scenario["resident_id"]
    case = ctx.store.case(ctx.incident_id, rid)
    spec = dict(scenario["result"])
    backstop = spec.pop("backstop", None)
    final = CheckinResult(language=ctx.store.resident(rid).language, confidence=0.9, **spec)
    if backstop:
        final.backstop = BackstopOutcome(**backstop)
    for n, kind in enumerate(scenario["attempts"], start=1):
        if n > 1:
            ctx.policy.requeue(case)
        attempt = ctx.policy.start_attempt(case, "simulated")
        if kind == "no_answer":
            attempt.answered = False
            ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.NO_ANSWER))
        elif kind == "unclear":
            ctx.policy.apply_result(
                case, CheckinResult(status=CheckinStatus.UNCLEAR, summary=final.summary)
            )
        else:
            ctx.policy.apply_result(case, final)
    ctx.store.save_case(case)
    if scenario.get("unavailable") == "all":
        for v in ctx.store.volunteers():
            if v.role == "volunteer":
                v.available = False
    return case, final


async def run_case(case: Case) -> dict[str, Any]:
    scenario = next(s for s in SCENARIOS if s["id"] == case.input["scenario"])
    rid = scenario["resident_id"]
    ctx = eval_context(
        f"eval-dispatch-{scenario['id']}-t{case.input['trial']}", [rid], auto_approve=True
    )
    record, result = _setup(ctx, scenario)
    snapshots: list[dict[str, Any]] = []
    error = ""
    answers: list[str] = []
    try:
        await dispatch(ctx, record, ctx.store.resident(rid), result)
        snapshots.append(_snapshot(ctx, rid))
        for _ in range(6):
            pending = [
                d
                for d in ctx.store.decisions(ctx.incident_id, status="pending")
                if d.resident_id == rid
            ]
            if not pending:
                break
            for d in pending:
                ids = [o.id for o in d.options]
                if d.name == VOLUNTEER_UPDATE:
                    choice, who = scenario.get("volunteer", "ok"), f"volunteer:{d.audience}"
                else:
                    choice, who = scenario.get("captain", {}).get(d.name, ids[0]), "captain:eval"
                choice = choice if choice in ids else ids[0]
                answers.append(f"{d.id} {d.name} -> {choice}")
                await respond_to_decision(
                    ctx,
                    d.id,
                    choice,
                    Responder(source="drill", external_id="eval"),
                    actor_override=who,
                )
                snapshots.append(_snapshot(ctx, rid))
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    events = ctx.audit.events()
    tools = [e.tool for e in events if e.type == "tool_call" and e.reason == "attempted" and e.tool]
    decisions = [d for d in ctx.store.decisions(ctx.incident_id) if d.resident_id == rid]
    captain = [d for d in decisions if d.audience == ctx.org.captain_id]
    final = _snapshot(ctx, rid)
    snapshots.append(final)
    observed = {
        "tools": tools,
        "captain_decisions": len(captain),
        "decision_names": [d.name for d in decisions],
        "answers": answers,
        "final_state": final["state"],
        "pending": final["pending"],
        "snapshots": snapshots,
        "denials": [f"{e.tool}: {e.reason}" for e in ctx.audit.denials()],
        "violations": find_violations(ctx),
        "messages": [f"{m.kind} -> {m.recipient}" for m in ctx.outbox],
        "history": [
            f"{t.from_state}->{t.to_state}" for t in ctx.store.case(ctx.incident_id, rid).history
        ],
        "error": error,
    }
    checks = scoring.trajectory_checks(scenario, observed)
    return {
        "output": {
            "scenario": scenario["id"],
            "trial": case.input["trial"],
            "checks": checks,
            "passed": all(checks.values()) and not error,
            **observed,
        },
        "trajectory": tools,
    }


class TrajectoryEvaluator(Evaluator[dict, dict]):
    def evaluate(self, data: EvaluationData[dict, dict]) -> list[EvaluationOutput]:
        out = data.actual_output or {}
        checks = out.get("checks", {})
        failed = [k for k, v in checks.items() if not v]
        return [
            EvaluationOutput(
                score=(sum(checks.values()) / len(checks)) if checks else 0.0,
                test_pass=bool(out.get("passed")),
                reason="all checks passed" if not failed else "failed: " + ", ".join(failed),
            )
        ]


async def run(
    label: str, *, trials: int = 2, only: list[str] | None = None, workers: int = 6
) -> dict[str, Any]:
    experiment = Experiment[dict, dict](
        cases=cases(trials, only), evaluators=[TrajectoryEvaluator()]
    )
    report = await experiment.run_evaluations_async(
        run_case,
        max_workers=workers,
        evaluation_data_store=LocalFileTaskResultStore(CACHE / f"{SUITE}-{label}"),
    )
    rows = sorted(
        (c["actual_output"] for c in report.cases if c.get("actual_output")),
        key=lambda r: (r["scenario"], r["trial"]),
    )
    check_names = sorted({k for r in rows for k in r["checks"]})
    return {
        "suite": SUITE,
        "label": label,
        "metrics": {
            "runs": len(rows),
            "scenarios": len({r["scenario"] for r in rows}),
            "runs_passed": sum(r["passed"] for r in rows),
            "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 4) if rows else None,
            "check_pass_rates": {
                k: round(
                    sum(r["checks"][k] for r in rows if k in r["checks"])
                    / max(1, sum(k in r["checks"] for r in rows)),
                    4,
                )
                for k in check_names
            },
            "double_decision_runs": sum(r["captain_decisions"] > 1 for r in rows),
            "resolved_with_pending_runs": sum(
                not r["checks"]["resolved_means_nothing_pending"] for r in rows
            ),
            "policy_violations": sum(len(r["violations"]) for r in rows),
            "failed": [
                f"{r['scenario']}#{r['trial']}: "
                + ", ".join(k for k, v in r["checks"].items() if not v)
                + (f" ({r['error']})" if r["error"] else "")
                for r in rows
                if not r["passed"]
            ],
        },
        "rows": rows,
    }
