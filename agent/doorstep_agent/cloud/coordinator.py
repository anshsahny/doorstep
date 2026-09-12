"""The Doorstep coordinator: every incident event, in one process per incident (SPEC §5).

Built with the Strands Agents SDK and hosted on Amazon Bedrock AgentCore Runtime. Each event
arrives as `{incident_id, event: {type, ...}}` on the runtime session derived from the incident,
so one incident's events share one microVM and one identity map (`store_dynamo`).

    replay            start a drill on the archived alert (the local DrillRunner, unchanged)
    alert             a live NWS alert: deterministic profile gate, then a drill incident
    checkin_result    a classified check-in: apply it and dispatch (Phase 4's voice bridge)
    decision_response a human's tap, forwarded by the Telegram webhook
    status            what the incident looks like now

Model-backed work runs as a tracked background task, so the invocation returns in about a second
and the webhook or API that forwarded it never hits a gateway timeout mid-resume. While any task
runs the runtime reports `HealthyBusy` and the session stays alive.

Every handler is safe to receive twice: starts and check-ins are claimed once in the store, a
decision leaves `pending` once (`claim_decision`), and deliveries are claimed before sending.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Protocol

from ..agents.dispatcher import dispatch
from ..audit import AuditLog
from ..config import Settings, settings
from ..decisions import expire_due_decisions, redrive_unapplied
from ..drill import DrillRunner
from ..models import Alert, CaseState, CheckinResult
from ..notify.base import Notifier, RecordingNotifier, deliver_pending
from ..notify.telegram import Bot, TelegramNotifier, process_tap
from ..profiles import load_profile
from ..runtime import RunContext, StoreOutbox
from ..state_machine import RETRYABLE, CasePolicy, Clock
from ..store import NotFound
from ..store_dynamo import DynamoBackend

log = logging.getLogger(__name__)

# A random id per process. Audit lines carry it, so "the resume ran in a different process from
# the one that paused" is something a test can read off the table rather than take on trust.
BOOT_ID = uuid.uuid4().hex[:12]
INCIDENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")


def runtime_session_id(incident_id: str) -> str:
    """The AgentCore session for an incident: 33–100 chars of `[a-zA-Z0-9-_]` (API reference).

    Keep in step with `api/doorstep_api/common.py`; a test compares the two.
    """
    base = f"doorstep-incident-{incident_id}"
    if len(base) < 33:
        base += "-" + hashlib.sha256(incident_id.encode()).hexdigest()[:16]
    return base[:100]


class TaskTracker(Protocol):
    def start(self, name: str) -> Any: ...
    def done(self, token: Any) -> None: ...


class Flags(Protocol):
    def kill_switch(self) -> bool: ...


class _NoTracker:
    def start(self, name: str) -> Any:
        return name

    def done(self, token: Any) -> None:
        return None


RunnerFactory = Callable[..., Any]


class Coordinator:
    def __init__(
        self,
        backend: DynamoBackend,
        *,
        flags: Flags,
        cfg: Settings | None = None,
        tracker: TaskTracker | None = None,
        bot_factory: Callable[[], Bot] | None = None,
        runner_factory: RunnerFactory = DrillRunner,
        model_override: Any = None,
    ) -> None:
        self.backend = backend
        self.flags = flags
        self.cfg = cfg or settings()
        self.tracker = tracker or _NoTracker()
        self.bot_factory = bot_factory
        self.runner_factory = runner_factory
        self.model_override = model_override
        self._runners: dict[str, Any] = {}
        self._contexts: dict[str, RunContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._telegram: TelegramNotifier | None = None

    # --- entry ---

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(payload.get("incident_id") or "")
        event = payload.get("event") or {}
        kind = str(event.get("type") or "")
        if not INCIDENT_ID.match(incident_id):
            return {"ok": False, "error": "incident_id is missing or malformed"}
        handlers: dict[str, Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "replay": self._replay,
            "alert": self._alert,
            "checkin_result": self._checkin_result,
            "decision_response": self._decision_response,
            "status": self._status,
        }
        handler = handlers.get(kind)
        if handler is None:
            return {"ok": False, "error": f"unknown event type {kind!r}"}
        if kind != "status" and self.flags.kill_switch():
            log.warning("kill switch is on; refused %s for %s", kind, incident_id)
            return {"ok": False, "error": "kill switch is on", "boot_id": BOOT_ID}
        result = await handler(incident_id, event)
        return {"boot_id": BOOT_ID, **result}

    async def drain(self) -> None:
        """Wait for every background task (tests, and a clean shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, name: str, work: Awaitable[Any]) -> None:
        token = self.tracker.start(name)

        async def run() -> None:
            try:
                await work
            except Exception:  # noqa: BLE001 - logged; the next event re-drives what it can
                log.exception("background task %s failed", name)
            finally:
                self.tracker.done(token)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _lock(self, incident_id: str) -> asyncio.Lock:
        return self._locks.setdefault(incident_id, asyncio.Lock())

    # --- contexts and channels ---

    def _notifier(self, telegram: bool) -> Notifier:
        """The real channel only when the incident asked for it; a record otherwise."""
        if not telegram:
            return RecordingNotifier()
        if self._telegram is None:
            if self.bot_factory is None:
                raise RuntimeError("this incident asks for Telegram but no bot is configured")
            self._telegram = TelegramNotifier(self.bot_factory())
        return self._telegram

    def _context(self, incident_id: str) -> RunContext:
        """The live context for an incident: the running drill's, or one rebuilt from the table."""
        runner = self._runners.get(incident_id)
        if runner is not None and getattr(runner, "ctx", None) is not None:
            return runner.ctx
        if incident_id in self._contexts:
            return self._contexts[incident_id]
        incident = self.backend.for_incident(incident_id).incident(incident_id)
        store = self.backend.for_incident(incident_id, incident.resident_ids or None)
        opts = incident.run_options
        clock = Clock(compression=float(opts.get("compression", 1.0)))
        cfg = replace(
            self.cfg,
            decision_ttl_minutes=float(
                opts.get("decision_ttl_minutes", self.cfg.decision_ttl_minutes)
            ),
        )
        ctx = RunContext(
            store=store,
            incident_id=incident_id,
            profile=load_profile(incident.profile_id),
            org=store.org(),
            mode=incident.mode,
            clock=clock,
            policy=CasePolicy(
                clock,
                max_attempts=cfg.max_attempts,
                retry_interval_minutes=cfg.retry_interval_minutes,
            ),
            audit=AuditLog(store, incident_id),
            settings=cfg,
            alert_severity=incident.alert.severity,
            auto_approve=bool(opts.get("auto_approve", False)),
            outbox=StoreOutbox(store, incident_id),
            model_override=self.model_override,
        )
        self._contexts[incident_id] = ctx
        return ctx

    def _note(self, ctx: RunContext, kind: str, reason: str, **data: Any) -> None:
        ctx.audit.record(
            actor="system:coordinator",
            type="note",
            reason=reason,
            data={"boot_id": BOOT_ID, "event": kind, **data},
        )

    # --- handlers ---

    async def _replay(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return self._start_drill(incident_id, event, alert=None)

    async def _alert(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A live alert. The profile's own event list decides before any model is asked."""
        alert = Alert.from_nws_feature(event.get("feature") or {})
        profile = load_profile(self.cfg.default_profile)
        if profile.activation_for(alert.event, alert.vtec_code()) is None:
            return {
                "ok": True,
                "accepted": False,
                "reason": f"{alert.event!r} is not a profile event",
            }
        return self._start_drill(incident_id, event, alert=alert)

    def _start_drill(
        self, incident_id: str, event: dict[str, Any], *, alert: Alert | None
    ) -> dict[str, Any]:
        if not self.backend.claim(f"START#{incident_id}"):
            return {"ok": True, "accepted": False, "reason": "incident already started"}
        telegram = bool(event.get("telegram", False))
        runner = self.runner_factory(
            auto_approve=bool(event.get("auto_approve", False)),
            timeout_seconds=float(event.get("timeout_seconds", 240.0)),
            decision_ttl_minutes=event.get("decision_ttl_minutes"),
            show_board=False,
            notifier=self._notifier(telegram),
            store_factory=self.backend.for_incident,
            incident_id=incident_id,
            alert=alert,
            run_options={"telegram": telegram},
            cfg=self.cfg,
        )
        self._runners[incident_id] = runner

        async def run() -> None:
            report = await runner.run()
            summary = {
                "all_settled": report.all_settled,
                "urgent_escalated": report.urgent_escalated,
                "violations": report.policy_violations,
                "wall_seconds": report.wall_seconds,
            }
            ctx = runner.ctx
            if ctx is not None:
                self._note(ctx, "replay", "drill loop finished", summary=summary)

        self._spawn(f"drill:{incident_id}", run())
        return {"ok": True, "accepted": True, "telegram": telegram}

    async def _checkin_result(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Apply one classified check-in and hand it to the dispatcher. Idempotent per attempt."""
        resident_id = str(event.get("resident_id") or "")
        attempt_key = str(event.get("attempt_key") or "")
        if not resident_id or not attempt_key:
            return {"ok": False, "error": "checkin_result needs resident_id and attempt_key"}
        try:
            result = CheckinResult.model_validate(event.get("result") or {})
            ctx = self._context(incident_id)
            case = ctx.store.case(incident_id, resident_id)
            resident = ctx.store.resident(resident_id)
        except (NotFound, ValueError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not self.backend.claim(f"CHECKIN#{incident_id}#{resident_id}#{attempt_key}"):
            return {"ok": True, "accepted": False, "reason": "this check-in was already applied"}

        async def work() -> None:
            async with self._lock(incident_id):
                if case.state == CaseState.QUEUED:
                    ctx.policy.start_attempt(case, str(event.get("channel") or "simulated"))
                ctx.policy.apply_result(case, result)
                ctx.store.save_case(case)
                self._note(ctx, "checkin_result", f"{resident_id}: {result.status}")
                if case.state not in RETRYABLE:
                    await dispatch(ctx, case, resident, result)
                    ctx.store.save_case(case)
                deliver_pending(ctx, self._notifier(_telegram(ctx)))

        self._spawn(f"checkin:{incident_id}:{resident_id}", work())
        return {"ok": True, "accepted": True}

    async def _decision_response(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        query = event.get("callback_query")
        if not isinstance(query, dict):
            return {"ok": False, "error": "decision_response needs a callback_query"}
        try:
            ctx = self._context(incident_id)
        except NotFound:
            return {"ok": False, "error": f"incident {incident_id} not found"}

        async def work() -> None:
            async with self._lock(incident_id):
                expire_due_decisions(ctx)
                telegram = _telegram(ctx)
                notifier = self._notifier(telegram)
                if isinstance(notifier, TelegramNotifier):
                    line = await notifier.handle_tap(ctx, query)
                    kind = (line or "").rsplit(": ", 1)[-1]
                else:
                    outcome = await process_tap(ctx, query)
                    kind = outcome.kind if outcome else "ignored"
                self._note(ctx, "decision_response", f"tap handled: {kind}", outcome=kind)
                redrive_unapplied(ctx)
                deliver_pending(ctx, notifier)

        self._spawn(f"decision:{incident_id}", work())
        return {"ok": True, "accepted": True}

    async def _status(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        try:
            ctx = self._context(incident_id)
        except NotFound:
            return {"ok": False, "error": f"incident {incident_id} not found"}
        incident = ctx.store.incident(incident_id)
        cases = ctx.store.cases(incident_id)
        counts: dict[str, int] = {}
        for c in cases:
            counts[str(c.state)] = counts.get(str(c.state), 0) + 1
        return {
            "ok": True,
            "status": incident.status,
            "cases": counts,
            "pending_decisions": [d.id for d in ctx.store.decisions(incident_id, "pending")],
            "running": incident_id in self._runners and bool(self._tasks),
        }


def _telegram(ctx: RunContext) -> bool:
    return bool(ctx.store.incident(ctx.incident_id).run_options.get("telegram", False))
