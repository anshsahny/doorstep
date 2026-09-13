"""The Doorstep coordinator: every incident event, in one process per incident (SPEC §5).

Built with the Strands Agents SDK and hosted on Amazon Bedrock AgentCore Runtime. Each event
arrives as `{incident_id, event: {type, ...}}` on the runtime session derived from the incident,
so one incident's events share one microVM and one identity map (`store_dynamo`).

    replay            start a drill on the archived alert (the local DrillRunner, unchanged)
    alert             a live NWS alert: deterministic profile gate, then a drill incident
    checkin_result    a classified check-in: apply it and dispatch
    checkin_urgent    a live call heard a red flag: page the captain now, while the call goes on
    checkin_attempt   a finished voice call's raw attempt: classify it here, apply, dispatch
    live_call         operator only: a one-resident live incident and one real check-in call,
                      decided by Cedar like any other call and queued for the dialer
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
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from strands import Agent

from ..agents.classifier import classify_attempt
from ..agents.dispatcher import dispatch
from ..agents.planned import PlannedAction
from ..audit import AuditHook, AuditLog
from ..backstop import find_red_flags
from ..config import Settings, settings
from ..decisions import Responder, expire_due_decisions, redrive_unapplied, respond_to_decision
from ..drill import DrillRunner
from ..models import (
    Alert,
    CaseState,
    CheckinAttempt,
    CheckinResult,
    CheckinStatus,
    ConversationTurn,
    Incident,
    ResidentCase,
)
from ..notify.base import Notifier, RecordingNotifier, deliver_pending
from ..notify.telegram import Bot, TelegramNotifier, process_tap
from ..policies import build_cedar
from ..profiles import load_profile
from ..risk import score_all
from ..runtime import RunContext, StoreOutbox
from ..state_machine import RETRYABLE, CasePolicy, Clock
from ..store import NotFound
from ..store_dynamo import DynamoBackend
from ..tools import place_checkin_call

log = logging.getLogger(__name__)

# A random id per process. Audit lines carry it, so "the resume ran in a different process from
# the one that paused" is something a test can read off the table rather than take on trust.
BOOT_ID = uuid.uuid4().hex[:12]
INCIDENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
RESIDENT_ID = re.compile(r"^r[0-9]{2}$")


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
        call_queue: Any = None,
    ) -> None:
        self.backend = backend
        self.call_queue = call_queue
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
            "sandbox": self._sandbox,
            "alert": self._alert,
            "checkin_result": self._checkin_result,
            "checkin_urgent": self._checkin_urgent,
            "checkin_attempt": self._checkin_attempt,
            "live_call": self._live_call,
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
            call_queue=self.call_queue,
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

    async def _sandbox(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A public visitor's drill. Whatever the event says, it never reaches a real channel:
        no Telegram, no simulated captain, and the incident is marked `sandbox` for Cedar."""
        voice = [str(r) for r in event.get("voice_residents") or []][:1]
        safe = {"voice_residents": voice, "timeout_seconds": 240.0}
        return self._start_drill(incident_id, safe, alert=None, mode="sandbox")

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
        self,
        incident_id: str,
        event: dict[str, Any],
        *,
        alert: Alert | None,
        mode: str = "drill",
    ) -> dict[str, Any]:
        if not self.backend.claim(f"START#{incident_id}"):
            return {"ok": True, "accepted": False, "reason": "incident already started"}
        telegram = mode == "drill" and bool(event.get("telegram", False))
        voice_residents = [str(r) for r in event.get("voice_residents") or []]
        runner = self.runner_factory(
            auto_approve=bool(event.get("auto_approve", False)),
            timeout_seconds=float(event.get("timeout_seconds", 240.0)),
            decision_ttl_minutes=event.get("decision_ttl_minutes"),
            show_board=False,
            notifier=self._notifier(telegram),
            store_factory=self.backend.for_incident,
            incident_id=incident_id,
            alert=alert,
            run_options={"telegram": telegram, "voice_residents": voice_residents},
            voice_residents=voice_residents,
            cfg=self.cfg,
            **({"mode": mode} if mode != "drill" else {}),
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

    # --- live calls ---

    async def _live_call(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A one-resident live incident and one real call (the phone gate and the video).

        Reached only by an IAM principal allowed to invoke this runtime (no public route). The
        call itself is a `place_checkin_call` through the agent loop, so Cedar, the tool's own
        code checks and the audit hook decide it exactly as they would a model's.
        """
        resident_id = str(event.get("resident_id") or "")
        if not RESIDENT_ID.match(resident_id) or not incident_id.startswith("live-"):
            return {"ok": False, "error": "live_call needs a live-… incident and a resident id"}
        if not self.backend.claim(f"START#{incident_id}"):
            return {"ok": True, "accepted": False, "reason": "incident already started"}
        store = self.backend.for_incident(incident_id, [resident_id])
        try:
            resident = store.resident(resident_id)
        except NotFound:
            return {"ok": False, "error": f"unknown resident {resident_id}"}
        profile = load_profile(self.cfg.default_profile)
        alert = Alert.from_fixture(
            json.loads(self.cfg.default_alert_fixture.read_text(encoding="utf-8"))
        )
        store.save_incident(
            Incident(
                id=incident_id,
                org_id=store.org().id,
                mode="live",
                profile_id=profile.id,
                alert=alert,
                status="active",
                resident_ids=[resident_id],
                run_options={
                    "telegram": bool(event.get("telegram", False)),
                    "operator_test": bool(event.get("operator_test", False)),
                    "voice_residents": [resident_id],
                    "decision_ttl_minutes": 30.0,
                },
            )
        )
        store.save_case(
            ResidentCase(
                incident_id=incident_id,
                resident_id=resident_id,
                risk=score_all([resident], profile)[resident_id],
            )
        )
        ctx = self._context(incident_id)
        self._note(
            ctx,
            "live_call",
            f"live incident for {resident_id}",
            operator_test=bool(event.get("operator_test", False)),
        )
        agent = Agent(
            name="outreach",
            model=PlannedAction(
                "place_checkin_call",
                {
                    "resident_id": resident_id,
                    "reason": "live check-in call requested by the operator",
                },
                f"live-call-{resident_id}",
            ),
            tools=[place_checkin_call],
            interventions=[build_cedar(ctx)],
            hooks=[AuditHook("agent:outreach")],
            callback_handler=None,
        )
        async with self._lock(incident_id):
            await agent.invoke_async(
                "place the call",
                invocation_state=ctx.invocation_state(
                    resident_id=resident_id, actor="agent:outreach"
                ),
            )
        outcome = ""
        for message in agent.messages:
            for block in message.get("content", []):
                if "toolResult" in block:
                    outcome = " ".join(
                        str(c.get("text", "")) for c in block["toolResult"].get("content", [])
                    )
        return {"ok": True, "accepted": True, "outcome": outcome}

    # --- voice ---

    def _voice_attempt(
        self, ctx: RunContext, case: ResidentCase, event: dict[str, Any], key: str
    ) -> CheckinAttempt | None:
        """The attempt a voice call belongs to: found by its token id, or started now."""
        channel = str(event.get("channel") or "")
        for attempt in reversed(case.attempt_log):
            if attempt.key == key:
                return attempt
        if case.state in RETRYABLE:
            ctx.policy.requeue(case)
        if case.state != CaseState.QUEUED:
            return None
        attempt = ctx.policy.start_attempt(
            case, channel if channel in ("browser", "phone") else "browser"
        )
        attempt.key = key
        try:
            # When the call began on the line, not when this event arrived.
            attempt.started_at = datetime.fromisoformat(str(event["started_at"]))
        except (KeyError, ValueError):
            pass
        return attempt

    async def _checkin_urgent(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A red flag heard mid-call. The captain is paged now; the call is still going on.

        The provisional result is URGENT with the categories the resident's own words match
        (or `other`). The final transcript arrives later as `checkin_attempt` and can only add.
        """
        resident_id = str(event.get("resident_id") or "")
        key = str(event.get("attempt_key") or "")
        if not resident_id or not key:
            return {"ok": False, "error": "checkin_urgent needs resident_id and attempt_key"}
        try:
            ctx = self._context(incident_id)
            resident = ctx.store.resident(resident_id)
            ctx.store.case(incident_id, resident_id)
        except NotFound as exc:
            return {"ok": False, "error": f"NotFound: {exc}"}
        if not self.backend.claim(f"URGENT#{incident_id}#{resident_id}#{key}"):
            return {"ok": True, "accepted": False, "reason": "this page was already sent"}
        reason = str(event.get("reason") or "red flag")[:300]
        source = str(event.get("source") or "agent")
        words = [str(w) for w in event.get("resident_words") or []][-3:]

        async def work() -> None:
            async with self._lock(incident_id):
                case = ctx.store.case(incident_id, resident_id)
                attempt = self._voice_attempt(ctx, case, event, key)
                if attempt is None or case.state != CaseState.CALLING:
                    self._note(
                        ctx,
                        "checkin_urgent",
                        f"{resident_id}: page not applied, case is {case.state}",
                    )
                    return
                if reason not in attempt.urgent_flags:
                    attempt.urgent_flags.append(reason)
                attempt.meta.update({"urgent_source": source, "urgent_at": event.get("at")})
                categories = sorted(
                    {m.category for m in find_red_flags(" ".join(words), ctx.profile)}
                )
                result = CheckinResult(
                    status=CheckinStatus.URGENT,
                    red_flags=categories or ["other"],
                    language=resident.language,
                    confidence=0.9,
                    key_quote=(words[-1] if words else "")[:200],
                    summary=f"Flagged urgent during the call ({source}): {reason}"[:300],
                    flagged_mid_call=True,
                )
                ctx.audit.record(
                    actor=f"system:voice-{source}",
                    type="checkin",
                    resident_id=resident_id,
                    reason=f"mid-call red flag, paging the captain before hang-up: {reason}",
                    data={"source": source, "at": event.get("at"), "boot_id": BOOT_ID},
                )
                ctx.policy.apply_result(case, result)
                attempt.ended_at = None  # the call is still going on
                ctx.store.save_case(case)
                await dispatch(ctx, case, resident, result)
                ctx.store.save_case(case)
                deliver_pending(ctx, self._notifier(_telegram(ctx)))

        self._spawn(f"urgent:{incident_id}:{resident_id}", work())
        return {"ok": True, "accepted": True}

    async def _checkin_attempt(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A finished voice call. Classified here, by the same layers as a text check-in."""
        resident_id = str(event.get("resident_id") or "")
        key = str(event.get("attempt_key") or "")
        if not resident_id or not key:
            return {"ok": False, "error": "checkin_attempt needs resident_id and attempt_key"}
        try:
            ctx = self._context(incident_id)
            resident = ctx.store.resident(resident_id)
            ctx.store.case(incident_id, resident_id)
            turns = [
                ConversationTurn(speaker=t["speaker"], text=str(t["text"])[:2000])
                for t in event.get("transcript") or []
                if isinstance(t, dict) and t.get("speaker") in ("agent", "resident")
            ][:200]
        except (NotFound, KeyError, ValueError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not self.backend.claim(f"CHECKIN#{incident_id}#{resident_id}#{key}"):
            return {"ok": True, "accepted": False, "reason": "this check-in was already applied"}

        async def work() -> None:
            async with self._lock(incident_id):
                case = ctx.store.case(incident_id, resident_id)
                attempt = self._voice_attempt(ctx, case, event, key)
                if attempt is None:
                    self._note(
                        ctx, "checkin_attempt", f"{resident_id}: not applied, case is {case.state}"
                    )
                    return
                attempt.transcript = turns
                attempt.answers = {
                    str(k)[:40]: str(v)[:200] for k, v in (event.get("answers") or {}).items()
                }
                for flag in event.get("urgent_flags") or []:
                    if str(flag) not in attempt.urgent_flags:
                        attempt.urgent_flags.append(str(flag)[:300])
                attempt.agent_summary = str(event.get("summary") or "")[:300]
                attempt.answered = bool(event.get("answered", True))
                attempt.ended_at = ctx.clock.now()
                attempt.meta.update(
                    {
                        k: event.get(k)
                        for k in (
                            "end_reason",
                            "ended_at",
                            "urgent_sent_at",
                            "urgent_source",
                            "page_delivered",
                            "interruptions",
                            "usage",
                        )
                        if event.get(k) is not None
                    }
                )
                # The transcript is on the board before the classifier has finished.
                ctx.store.save_case(case)
                final = await classify_attempt(ctx, resident, attempt)
                if case.state == CaseState.CALLING:
                    ctx.policy.apply_result(case, final)
                    ctx.store.save_case(case)
                    self._note(ctx, "checkin_attempt", f"{resident_id}: {final.status}")
                    if case.state not in RETRYABLE:
                        await dispatch(ctx, case, resident, final)
                        ctx.store.save_case(case)
                else:
                    # Paged mid-call: the case already moved on. The final reading may add
                    # needs and flags; it never lowers the status.
                    earlier = attempt.result
                    if earlier is not None and final.status != CheckinStatus.URGENT:
                        ctx.audit.record(
                            actor="system:mid-call-flag",
                            type="backstop",
                            resident_id=resident_id,
                            reason=(
                                f"final classification {final.status} after a mid-call page; "
                                "kept URGENT (never lowered)"
                            ),
                        )
                        final.status = CheckinStatus.URGENT
                    if earlier is not None:
                        final.red_flags = sorted(set(final.red_flags) | set(earlier.red_flags))
                    attempt.result = final
                    if case.results:
                        case.results[-1] = final
                    ctx.store.save_case(case)
                    self._note(
                        ctx,
                        "checkin_attempt",
                        f"{resident_id}: final {final.status} (paged mid-call)",
                    )
                deliver_pending(ctx, self._notifier(_telegram(ctx)))

        self._spawn(f"attempt:{incident_id}:{resident_id}", work())
        return {"ok": True, "accepted": True}

    async def _decision_response(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """A tap from Telegram (`callback_query`) or the dashboard (`web`). Both end in the same
        `respond_to_decision`, under the same lock, followed by the same redrive and delivery."""
        query = event.get("callback_query")
        web = event.get("web")
        if isinstance(web, dict):
            query = None
            fields = [str(web.get(k) or "") for k in ("decision_id", "option_id", "subject")]
            if not all(fields):
                return {
                    "ok": False,
                    "error": "a web decision needs decision_id, option_id, subject",
                }
        elif not isinstance(query, dict):
            return {"ok": False, "error": "decision_response needs a callback_query or web"}
        try:
            ctx = self._context(incident_id)
        except NotFound:
            return {"ok": False, "error": f"incident {incident_id} not found"}

        async def work() -> None:
            async with self._lock(incident_id):
                expire_due_decisions(ctx)
                telegram = _telegram(ctx)
                notifier = self._notifier(telegram)
                if query is None:
                    decision_id, option_id, subject = fields
                    outcome = await respond_to_decision(
                        ctx, decision_id, option_id, Responder(source="web", external_id=subject)
                    )
                    kind = outcome.kind
                    if outcome.applied and isinstance(notifier, TelegramNotifier):
                        # Keep the captain's phone honest: its buttons now say it was answered.
                        try:
                            notifier.confirm(ctx, outcome.decision, outcome.message)
                        except Exception:  # noqa: BLE001 - an edit is cosmetic; the answer stands
                            log.warning("could not edit the Telegram message for %s", decision_id)
                elif isinstance(notifier, TelegramNotifier):
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
