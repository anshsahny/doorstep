"""Local drill runner (PLAN Phase 1 task 9): the whole incident with simulated residents.

    alert fixture -> Strands Graph (assess -> triage -> outreach) -> check-ins (concurrent)
    -> classifier + backstop -> dispatcher (Cedar + audit) -> retries / escalations -> report

Nothing leaves the process: no calls, no Telegram. Messages are recorded in `ctx.outbox`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
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
from .decisions import Responder, expire_due_decisions, redrive_unapplied, respond_to_decision
from .graph import start_incident
from .models import Alert, CaseState, Incident, ResidentCase, utcnow
from .notify.base import Notifier, RecordingNotifier, deliver_pending
from .profiles import load_profile
from .runtime import RunContext, StoreOutbox
from .state_machine import CasePolicy, Clock, is_settled
from .store import InMemoryStore, Repository, load_drill_subset
from .violations import find_violations

# Builds the store for one incident from its id and resident subset. Local drills use the
# default (in memory, from `data/`); the cloud coordinator passes a DynamoDB-backed one.
StoreFactory = Callable[[str, list[str]], Repository]


def new_incident_id() -> str:
    return "drill-" + utcnow().strftime("%Y%m%d-%H%M%S")


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
        notifier: Notifier | None = None,
        notifier_taps: Callable[[RunContext], Awaitable[list[str]]] | None = None,
        decision_ttl_minutes: float | None = None,
        store_factory: StoreFactory | None = None,
        incident_id: str | None = None,
        alert: Alert | None = None,
        run_options: dict[str, Any] | None = None,
    ) -> None:
        self.cfg = cfg or settings()
        self.store_factory = store_factory
        self.incident_id = incident_id
        self.alert = alert
        self.run_options = dict(run_options or {})
        if decision_ttl_minutes is not None:
            # Real minutes, never compressed: see `decisions.expires_at`.
            self.cfg = replace(self.cfg, decision_ttl_minutes=decision_ttl_minutes)
        # Nothing leaves the process unless a real channel is passed in on purpose.
        self.notifier: Notifier = notifier or RecordingNotifier()
        self.notifier_taps = notifier_taps
        self.taps: list[str] = []
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
        incident_id = self.incident_id or new_incident_id()
        store = (
            self.store_factory(incident_id, resident_ids)
            if self.store_factory
            else InMemoryStore.from_data_dir(self.cfg.data_dir, resident_ids=resident_ids)
        )
        clock = Clock(compression=self.compression)
        alert = self.alert or Alert.from_fixture(
            json.loads(self.alert_path.read_text(encoding="utf-8"))
        )
        store.save_incident(
            Incident(
                id=incident_id,
                org_id=store.org().id,
                mode="drill",
                profile_id=self.profile.id,
                alert=alert,
                resident_ids=resident_ids,
                run_options={
                    "compression": self.compression,
                    "auto_approve": self.auto_approve,
                    "decision_ttl_minutes": self.cfg.decision_ttl_minutes,
                    **self.run_options,
                },
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
            outbox=StoreOutbox(store, incident_id),
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

    # --- decisions ---

    async def _handle_decisions(self) -> None:
        """Expire what nobody answered, deliver what is new, and collect answers.

        Every path here ends in `respond_to_decision`, including the unattended drill's simulated
        captain, so `--auto-approve` exercises the same resume and idempotency machinery a real
        captain's thumb does rather than a shortcut around it.
        """
        ctx = self.ctx
        assert ctx is not None
        expire_due_decisions(ctx)
        redrive_unapplied(ctx)
        deliver_pending(ctx, self.notifier)
        if self.notifier_taps is not None:
            self.taps.extend(await self.notifier_taps(ctx))
        if not self.auto_approve:
            return
        for decision in ctx.store.decisions(ctx.incident_id, status="pending"):
            await respond_to_decision(
                ctx,
                decision.id,
                decision.options[0].id,
                Responder(source="drill", external_id="auto-approve"),
                actor_override="captain:auto-approve",
            )

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
            await self._handle_decisions()
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
            # A decision nobody has answered yet keeps the drill alive: with a real captain on
            # the other end, the run is not finished, it is waiting.
            waiting = bool(ctx.store.decisions(ctx.incident_id, status="pending"))
            settled = all(is_settled(c) for c in cases)
            if settled and not active and not waiting:
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

        await self._handle_decisions()
        self._refresh()
        return self._report(activated=True)

    def _report(self, *, activated: bool) -> DrillReport:
        assert self.ctx is not None
        return build_report(
            self.ctx,
            self.personas,
            activated=activated,
            started_at=self.started_at,
            wall_seconds=round(self.elapsed(), 1),
        )


def build_report(
    ctx: RunContext,
    personas: dict[str, Persona],
    *,
    activated: bool,
    started_at: datetime,
    wall_seconds: float,
) -> DrillReport:
    """The drill report from whatever store the context reads: memory, or DynamoDB in the cloud."""
    cases = sorted(ctx.store.cases(ctx.incident_id), key=lambda c: (c.risk.wave, -c.risk.points))
    urgent_expected = sorted(
        rid for rid, p in personas.items() if p.ground_truth.status == "URGENT"
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
        started_at=started_at,
        ended_at=ended,
        wall_seconds=wall_seconds,
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
                    c.latest_result and c.latest_result.backstop and c.latest_result.backstop.raised
                ),
                "expected": (
                    personas[c.resident_id].ground_truth.status
                    if c.resident_id in personas
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
