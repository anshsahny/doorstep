"""Incident case state machine and retry timers (SPEC §6.3).

    QUEUED -> CALLING -> (OK | NEEDS_HELP | URGENT | NO_ANSWER | UNCLEAR)
    NO_ANSWER / UNCLEAR -> retry (QUEUED) up to `max_attempts`, then ESCALATED
    NEEDS_HELP -> ASSIGNED -> RESOLVED      (or ESCALATED when nobody can help)
    URGENT -> ESCALATED -> RESOLVED
    OK -> RESOLVED, with a re-check scheduled

Timers are expressed in "incident minutes" and converted to wall-clock seconds by a `Clock`, so a
drill can compress 10 minutes to 20 seconds without touching the rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    CaseState,
    CheckinAttempt,
    CheckinResult,
    CheckinStatus,
    ResidentCase,
    Transition,
    utcnow,
)

ALLOWED: dict[CaseState, frozenset[CaseState]] = {
    CaseState.QUEUED: frozenset({CaseState.CALLING}),
    CaseState.CALLING: frozenset(
        {
            CaseState.OK,
            CaseState.NEEDS_HELP,
            CaseState.URGENT,
            CaseState.NO_ANSWER,
            CaseState.UNCLEAR,
        }
    ),
    CaseState.OK: frozenset({CaseState.RESOLVED}),
    CaseState.NEEDS_HELP: frozenset({CaseState.ASSIGNED, CaseState.ESCALATED, CaseState.RESOLVED}),
    CaseState.URGENT: frozenset({CaseState.ESCALATED}),
    CaseState.NO_ANSWER: frozenset({CaseState.QUEUED, CaseState.ESCALATED}),
    CaseState.UNCLEAR: frozenset({CaseState.QUEUED, CaseState.ESCALATED}),
    CaseState.ASSIGNED: frozenset({CaseState.RESOLVED, CaseState.ESCALATED}),
    CaseState.ESCALATED: frozenset({CaseState.ASSIGNED, CaseState.RESOLVED}),
    CaseState.RESOLVED: frozenset(),
}

TERMINAL: frozenset[CaseState] = frozenset({CaseState.RESOLVED})
AWAITING_HUMAN: frozenset[CaseState] = frozenset({CaseState.ESCALATED})
RETRYABLE: frozenset[CaseState] = frozenset({CaseState.NO_ANSWER, CaseState.UNCLEAR})

RESULT_TO_STATE: dict[CheckinStatus, CaseState] = {
    CheckinStatus.OK: CaseState.OK,
    CheckinStatus.NEEDS_HELP: CaseState.NEEDS_HELP,
    CheckinStatus.URGENT: CaseState.URGENT,
    CheckinStatus.NO_ANSWER: CaseState.NO_ANSWER,
    CheckinStatus.UNCLEAR: CaseState.UNCLEAR,
}


class IllegalTransition(ValueError):
    pass


class Clock:
    """Wall-clock time with a compression factor for drills (SPEC §6.3: 10 min -> 20 s)."""

    def __init__(self, compression: float = 1.0) -> None:
        if compression <= 0:
            raise ValueError("compression must be positive")
        self.compression = compression

    def now(self) -> datetime:
        return utcnow()

    def seconds_for(self, incident_minutes: float) -> float:
        return incident_minutes * 60.0 / self.compression

    def after(self, incident_minutes: float) -> datetime:
        return self.now() + timedelta(seconds=self.seconds_for(incident_minutes))


def transition(case: ResidentCase, to: CaseState, reason: str = "") -> Transition:
    """Move a case to a new state or raise IllegalTransition. Mutates and returns the record."""
    if to not in ALLOWED[case.state]:
        raise IllegalTransition(f"{case.resident_id}: {case.state} -> {to} is not allowed")
    when = utcnow()
    record = Transition(from_state=case.state, to_state=to, at=when, reason=reason)
    case.state = to
    case.updated_at = when
    case.history.append(record)
    return record


def is_terminal(case: ResidentCase) -> bool:
    return case.state in TERMINAL


def is_settled(case: ResidentCase) -> bool:
    """Terminal, or parked on a human decision (Phase 1 treats ESCALATED as settled)."""
    return case.state in TERMINAL or case.state in AWAITING_HUMAN


class CasePolicy:
    """Applies check-in results and retry rules to a case."""

    def __init__(
        self, clock: Clock, max_attempts: int = 3, retry_interval_minutes: float = 10
    ) -> None:
        self.clock = clock
        self.max_attempts = max_attempts
        self.retry_interval_minutes = retry_interval_minutes

    def start_attempt(self, case: ResidentCase, channel: str) -> CheckinAttempt:
        transition(case, CaseState.CALLING, reason=f"attempt {case.attempts + 1} via {channel}")
        case.attempts += 1
        case.next_action_at = None
        attempt = CheckinAttempt(attempt=case.attempts, channel=channel)  # type: ignore[arg-type]
        case.attempt_log.append(attempt)
        return attempt

    def apply_result(self, case: ResidentCase, result: CheckinResult) -> Transition:
        """Record a result and move the case. Retryable results schedule the next attempt."""
        if case.attempt_log:
            attempt = case.attempt_log[-1]
            attempt.result = result
            attempt.ended_at = self.clock.now()
            attempt.answered = result.status not in (CheckinStatus.NO_ANSWER,)
        case.results.append(result)
        to = RESULT_TO_STATE[result.status]
        record = transition(case, to, reason=f"check-in result {result.status}")
        if to in RETRYABLE:
            if case.attempts < self.max_attempts:
                case.next_action_at = self.clock.after(self.retry_interval_minutes)
            else:
                transition(
                    case,
                    CaseState.ESCALATED,
                    reason=f"{result.status} after {case.attempts} attempts",
                )
        return record

    def retry_due(self, case: ResidentCase, now: datetime | None = None) -> bool:
        if case.state not in RETRYABLE or case.next_action_at is None:
            return False
        return (now or self.clock.now()) >= case.next_action_at

    def requeue(self, case: ResidentCase) -> Transition:
        return transition(case, CaseState.QUEUED, reason=f"retry {case.attempts + 1}")

    def schedule_recheck(self, case: ResidentCase, minutes: float) -> datetime:
        case.recheck_at = self.clock.after(minutes)
        return case.recheck_at
