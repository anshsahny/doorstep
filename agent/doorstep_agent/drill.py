"""Local drill runner (PLAN Phase 1 task 9): the whole incident with simulated residents.

    alert fixture -> Strands Graph (assess -> triage -> outreach) -> check-ins (concurrent)
    -> classifier + backstop -> dispatcher (Cedar + audit) -> retries / escalations -> report

Nothing leaves the process: no calls, no Telegram. Messages are recorded in `ctx.outbox`.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import board
from .agents.checkin_text import run_text_checkin
from .agents.classifier import classify_attempt
from .agents.dispatcher import dispatch
from .agents.persona import NoAnswerChannel, Persona, PersonaChannel, load_personas
from .audit import AuditLog
from .config import Settings, settings
from .graph import start_incident
from .models import Alert, CaseState, Incident, ResidentCase, utcnow
from .profiles import load_profile
from .runtime import RunContext
from .state_machine import CasePolicy, Clock, is_settled, transition
from .store import InMemoryStore, load_drill_subset
from .violations import find_violations


@dataclass
class DrillReport:
    incident_id: str
    profile_id: str
    activated: bool
    started_at: datetime
    ended_at: datetime
    wall_seconds: float
    cases: list[dict[str, Any]]
    all_settled: bool
    unsettled: list[str]
    urgent_expected: list[str]
    urgent_escalated: list[str]
    both_urgent_escalated: bool
    policy_denials: int
    policy_violations: int
    violations: list[str]
    decisions: list[dict[str, Any]]
    messages: int
    audit_events: int
    corrections: list[str] = field(default_factory=list)

    def gate_passed(self, limit_seconds: float = 240.0) -> bool:
        return (
            self.activated
            and self.all_settled
            and self.both_urgent_escalated
            and self.policy_violations == 0
            and self.wall_seconds < limit_seconds
        )

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=str) + "\n"


class DrillRunner:
    def __init__(
        self,
        *,
        profile_id: str | None = None,
        alert_path: Path | None = None,
        auto_approve: bool = True,
        concurrency: int | None = None,
        compression: float | None = None,
        timeout_seconds: float = 240.0,
        show_board: bool = True,
        clear_screen: bool = True,
        cfg: Settings | None = None,
    ) -> None:
        self.cfg = cfg or settings()
        self.profile = load_profile(profile_id or self.cfg.default_profile)
        self.alert_path = alert_path or self.cfg.default_alert_fixture
        self.auto_approve = auto_approve
        self.concurrency = concurrency or self.cfg.drill_concurrency
        self.compression = compression or self.cfg.drill_time_compression
        self.timeout_seconds = timeout_seconds
        self.show_board = show_board
        self.clear_screen = clear_screen
        self.ctx: RunContext | None = None
        self.personas: dict[str, Persona] = {}
        self.started_at = utcnow()
        self._t0 = time.monotonic()

    # --- setup ---

    def _build_context(self) -> RunContext:
        # A profile without a persona set can still be assessed; it just cannot simulate calls.
        personas = (
            load_personas(self.cfg.data_dir.parent / self.profile.eval_personas)
            if self.profile.eval_personas
            else []
        )
        self.personas = {p.resident_id: p for p in personas}
        resident_ids = list(self.personas) or load_drill_subset(self.cfg.data_dir)
        store = InMemoryStore.from_data_dir(self.cfg.data_dir, resident_ids=resident_ids)
        clock = Clock(compression=self.compression)
        incident_id = "drill-" + utcnow().strftime("%Y%m%d-%H%M%S")
        alert = Alert.from_fixture(json.loads(self.alert_path.read_text(encoding="utf-8")))
        store.save_incident(
            Incident(
                id=incident_id,
                org_id=store.org().id,
                mode="drill",
                profile_id=self.profile.id,
                alert=alert,
            )
        )
        ctx = RunContext(
            store=store,
            incident_id=incident_id,
            profile=self.profile,
            org=store.org(),
            mode="drill",
            clock=clock,
            policy=CasePolicy(
                clock,
                max_attempts=self.cfg.max_attempts,
                retry_interval_minutes=self.cfg.retry_interval_minutes,
            ),
            audit=AuditLog(store, incident_id),
            settings=self.cfg,
            alert_severity=alert.severity,
            auto_approve=self.auto_approve,
        )
        self.ctx = ctx
        return ctx

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def _refresh(self) -> None:
        if self.show_board and self.ctx is not None:
            board.show(board.render(self.ctx, wall_seconds=self.elapsed()), clear=self.clear_screen)

    # --- one case ---

    async def process_case(self, case: ResidentCase) -> None:
        ctx = self.ctx
        assert ctx is not None
        resident = ctx.store.resident(case.resident_id)
        persona = self.personas[case.resident_id]
        attempt = ctx.policy.start_attempt(case, "simulated")
        ctx.store.save_case(case)
        self._refresh()
        channel = (
            NoAnswerChannel()
            if persona.behaviour == "no_answer"
            else PersonaChannel(persona, self.cfg.model_persona)
        )
        await run_text_checkin(ctx, resident, attempt, channel)
        result = await classify_attempt(ctx, resident, attempt)
        ctx.policy.apply_result(case, result)
        ctx.store.save_case(case)
        self._refresh()
        # Retryable results wait for the timer; everything else goes to the dispatcher, including
        # a case the retry policy just escalated after its last unanswered attempt.
        if case.state not in (CaseState.NO_ANSWER, CaseState.UNCLEAR):
            await dispatch(ctx, case, resident, result)
            ctx.store.save_case(case)
        self._refresh()

    # --- decisions (Phase 1: a simulated captain when --auto-approve is set) ---

    def _auto_answer_decisions(self) -> None:
        ctx = self.ctx
        assert ctx is not None
        if not self.auto_approve:
            return
        for decision in ctx.store.decisions(ctx.incident_id, status="pending"):
            option = decision.options[0]
            decision.status, decision.responder, decision.response = (
                "answered",
                "captain:auto-approve",
                option.id,
            )
            decision.responded_at = ctx.clock.now()
            ctx.store.save_decision(decision)
            ctx.audit.record(
                actor="captain:auto-approve",
                type="decision",
                resident_id=decision.resident_id,
                reason=f"{decision.id} [{decision.name}] -> {option.label}",
            )
            if option.action == "resolve" and decision.resident_id:
                case = ctx.store.case(ctx.incident_id, decision.resident_id)
                if case.state == CaseState.ESCALATED:
                    transition(case, CaseState.RESOLVED, reason=f"captain: {option.label}")
                    case.outcome = "captain handling"
                    ctx.store.save_case(case)

    # --- main loop ---

    async def run(self) -> DrillReport:
        ctx = self._build_context()
        alert = ctx.store.incident(ctx.incident_id).alert
        self._refresh()
        incident = await start_incident(ctx, alert)
        self._refresh()
        if incident.status != "active":
            return self._report(activated=False)
        if not self.personas:
            ctx.audit.record(
                actor="system:drill",
                type="note",
                reason=f"profile {ctx.profile.id} has no persona set; check-ins not simulated",
            )
            return self._report(activated=True)

        semaphore = asyncio.Semaphore(self.concurrency)
        running: dict[str, asyncio.Task[None]] = {}

        async def guarded(case: ResidentCase) -> None:
            async with semaphore:
                try:
                    await self.process_case(case)
                except Exception as exc:  # noqa: BLE001 - keep the drill going
                    ctx.audit.record(
                        actor="system:drill",
                        type="note",
                        resident_id=case.resident_id,
                        reason=f"case failed: {type(exc).__name__}: {exc}",
                    )

        order = {rid: i for i, rid in enumerate(ctx.extras.get("queued", []))}
        while True:
            self._auto_answer_decisions()
            cases = sorted(
                ctx.store.cases(ctx.incident_id), key=lambda c: order.get(c.resident_id, 99)
            )
            now = ctx.clock.now()
            for case in cases:
                if case.resident_id in running and not running[case.resident_id].done():
                    continue
                if ctx.policy.retry_due(case, now):
                    ctx.policy.requeue(case)
                    ctx.store.save_case(case)
                if case.state == CaseState.QUEUED and (
                    case.next_action_at is None or now >= case.next_action_at
                ):
                    running[case.resident_id] = asyncio.create_task(guarded(case))
            active = [t for t in running.values() if not t.done()]
            settled = all(is_settled(c) for c in cases)
            if settled and not active:
                break
            if self.elapsed() > self.timeout_seconds:
                ctx.audit.record(
                    actor="system:drill", type="note", reason="drill timeout reached; stopping"
                )
                for t in active:
                    t.cancel()
                await asyncio.gather(*active, return_exceptions=True)
                break
            self._refresh()
            await asyncio.sleep(1.0)

        self._auto_answer_decisions()
        self._refresh()
        return self._report(activated=True)

    def _report(self, *, activated: bool) -> DrillReport:
        ctx = self.ctx
        assert ctx is not None
        cases = sorted(
            ctx.store.cases(ctx.incident_id), key=lambda c: (c.risk.wave, -c.risk.points)
        )
        urgent_expected = sorted(
            rid for rid, p in self.personas.items() if p.ground_truth.status == "URGENT"
        )
        urgent_escalated = sorted(
            c.resident_id
            for c in cases
            if c.resident_id in urgent_expected
            and any(t.to_state == CaseState.ESCALATED for t in c.history)
        )
        unsettled = [c.resident_id for c in cases if not is_settled(c)]
        violations = find_violations(ctx)
        ended = utcnow()
        return DrillReport(
            incident_id=ctx.incident_id,
            profile_id=ctx.profile.id,
            activated=activated,
            started_at=self.started_at,
            ended_at=ended,
            wall_seconds=round(self.elapsed(), 1),
            cases=[
                {
                    "resident_id": c.resident_id,
                    "wave": c.risk.wave,
                    "points": c.risk.points,
                    "state": str(c.state),
                    "attempts": c.attempts,
                    "result": str(c.latest_result.status) if c.latest_result else None,
                    "needs": c.latest_result.needs if c.latest_result else [],
                    "red_flags": c.latest_result.red_flags if c.latest_result else [],
                    "backstop_raised": bool(
                        c.latest_result
                        and c.latest_result.backstop
                        and c.latest_result.backstop.raised
                    ),
                    "expected": (
                        self.personas[c.resident_id].ground_truth.status
                        if c.resident_id in self.personas
                        else None
                    ),
                    "outcome": c.outcome,
                    "assigned_volunteer": c.assigned_volunteer,
                    "history": [f"{t.from_state}->{t.to_state}" for t in c.history],
                }
                for c in cases
            ],
            all_settled=not unsettled and bool(cases),
            unsettled=unsettled,
            urgent_expected=urgent_expected,
            urgent_escalated=urgent_escalated,
            both_urgent_escalated=bool(urgent_expected) and urgent_escalated == urgent_expected,
            policy_denials=len(ctx.audit.denials()),
            policy_violations=len(violations),
            violations=violations,
            decisions=[
                {
                    "id": d.id,
                    "name": d.name,
                    "resident_id": d.resident_id,
                    "status": d.status,
                    "response": d.response,
                }
                for d in ctx.store.decisions(ctx.incident_id)
            ],
            messages=len(ctx.outbox),
            audit_events=len(ctx.audit.events()),
            corrections=list(ctx.extras.get("plan_corrections", [])),
        )
