"""Every transition of the case state machine (SPEC §6.3), retries and the drill clock."""

from datetime import timedelta

import pytest

from doorstep_agent.models import CaseState, CheckinResult, CheckinStatus, ResidentCase, RiskScore
from doorstep_agent.state_machine import (
    ALLOWED,
    AWAITING_HUMAN,
    TERMINAL,
    CasePolicy,
    Clock,
    IllegalTransition,
    is_settled,
    is_terminal,
    transition,
)


def _case(state: CaseState = CaseState.QUEUED) -> ResidentCase:
    case = ResidentCase(
        incident_id="inc", resident_id="r1", risk=RiskScore(resident_id="r1", points=9, wave=1)
    )
    case.state = state
    return case


LEGAL = [
    (CaseState.QUEUED, CaseState.CALLING),
    (CaseState.CALLING, CaseState.OK),
    (CaseState.CALLING, CaseState.NEEDS_HELP),
    (CaseState.CALLING, CaseState.URGENT),
    (CaseState.CALLING, CaseState.NO_ANSWER),
    (CaseState.CALLING, CaseState.UNCLEAR),
    (CaseState.OK, CaseState.RESOLVED),
    (CaseState.NEEDS_HELP, CaseState.ASSIGNED),
    (CaseState.NEEDS_HELP, CaseState.ESCALATED),
    (CaseState.NEEDS_HELP, CaseState.RESOLVED),
    (CaseState.URGENT, CaseState.ESCALATED),
    (CaseState.NO_ANSWER, CaseState.QUEUED),
    (CaseState.NO_ANSWER, CaseState.ESCALATED),
    (CaseState.UNCLEAR, CaseState.QUEUED),
    (CaseState.UNCLEAR, CaseState.ESCALATED),
    (CaseState.ASSIGNED, CaseState.RESOLVED),
    (CaseState.ASSIGNED, CaseState.ESCALATED),
    (CaseState.ESCALATED, CaseState.ASSIGNED),
    (CaseState.ESCALATED, CaseState.RESOLVED),
]


def test_legal_table_is_exactly_the_spec() -> None:
    expected = {}
    for a, b in LEGAL:
        expected.setdefault(a, set()).add(b)
    assert {k: set(v) for k, v in ALLOWED.items() if v} == expected
    assert ALLOWED[CaseState.RESOLVED] == frozenset()


@pytest.mark.parametrize(("start", "end"), LEGAL)
def test_every_legal_transition(start: CaseState, end: CaseState) -> None:
    case = _case(start)
    record = transition(case, end, reason="test")
    assert case.state == end
    assert record.from_state == start and record.to_state == end and record.reason == "test"
    assert case.history[-1] == record


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (CaseState.QUEUED, CaseState.OK),
        (CaseState.OK, CaseState.URGENT),
        (CaseState.URGENT, CaseState.RESOLVED),  # must go through ESCALATED
        (CaseState.RESOLVED, CaseState.QUEUED),
        (CaseState.CALLING, CaseState.ESCALATED),
        (CaseState.NO_ANSWER, CaseState.OK),
    ],
)
def test_illegal_transitions_raise(start: CaseState, end: CaseState) -> None:
    case = _case(start)
    with pytest.raises(IllegalTransition):
        transition(case, end)
    assert case.state == start
    assert case.history == []


def test_terminal_and_settled_sets() -> None:
    assert TERMINAL == {CaseState.RESOLVED}
    assert AWAITING_HUMAN == {CaseState.ESCALATED, CaseState.ASSIGNED}
    assert is_terminal(_case(CaseState.RESOLVED))
    assert not is_terminal(_case(CaseState.ESCALATED))
    assert is_settled(_case(CaseState.ESCALATED))
    assert not is_settled(_case(CaseState.NEEDS_HELP))


def test_clock_compression() -> None:
    real = Clock()
    drill = Clock(compression=30)
    assert real.seconds_for(10) == 600
    assert drill.seconds_for(10) == 20
    assert drill.after(10) - drill.now() <= timedelta(seconds=20.5)
    with pytest.raises(ValueError):
        Clock(compression=0)


def test_no_answer_retries_three_times_then_escalates() -> None:
    policy = CasePolicy(Clock(compression=30), max_attempts=3, retry_interval_minutes=10)
    case = _case()
    for attempt in (1, 2):
        policy.start_attempt(case, "simulated")
        assert case.state == CaseState.CALLING and case.attempts == attempt
        policy.apply_result(case, CheckinResult(status=CheckinStatus.NO_ANSWER))
        assert case.state == CaseState.NO_ANSWER
        assert case.next_action_at is not None
        assert not policy.retry_due(case, now=policy.clock.now())
        assert policy.retry_due(case, now=policy.clock.now() + timedelta(seconds=21))
        policy.requeue(case)
        assert case.state == CaseState.QUEUED
    policy.start_attempt(case, "simulated")
    policy.apply_result(case, CheckinResult(status=CheckinStatus.NO_ANSWER))
    assert case.attempts == 3
    assert case.state == CaseState.ESCALATED
    assert case.next_action_at is None
    assert [t.to_state for t in case.history][-2:] == [CaseState.NO_ANSWER, CaseState.ESCALATED]
    assert case.attempt_log[-1].answered is False


def test_unclear_is_treated_like_no_answer() -> None:
    policy = CasePolicy(Clock(compression=30), max_attempts=2)
    case = _case()
    policy.start_attempt(case, "simulated")
    policy.apply_result(case, CheckinResult(status=CheckinStatus.UNCLEAR))
    assert case.state == CaseState.UNCLEAR and case.next_action_at is not None
    policy.requeue(case)
    policy.start_attempt(case, "simulated")
    policy.apply_result(case, CheckinResult(status=CheckinStatus.UNCLEAR))
    assert case.state == CaseState.ESCALATED


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (CheckinStatus.OK, CaseState.OK),
        (CheckinStatus.NEEDS_HELP, CaseState.NEEDS_HELP),
        (CheckinStatus.URGENT, CaseState.URGENT),
    ],
)
def test_answered_results_move_the_case_and_record_the_attempt(status, state) -> None:
    policy = CasePolicy(Clock())
    case = _case()
    policy.start_attempt(case, "simulated")
    policy.apply_result(case, CheckinResult(status=status, summary="x"))
    assert case.state == state
    assert case.results[-1].status == status
    assert case.attempt_log[-1].result is not None and case.attempt_log[-1].answered
    assert case.next_action_at is None


def test_recheck_scheduling_uses_the_clock() -> None:
    policy = CasePolicy(Clock(compression=60))
    case = _case(CaseState.OK)
    when = policy.schedule_recheck(case, 240)
    assert case.recheck_at == when
    assert when - policy.clock.now() <= timedelta(seconds=240.5)
