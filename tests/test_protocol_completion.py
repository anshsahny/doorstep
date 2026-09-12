"""A call that never establishes how the resident is cannot be classified OK.

The phrase backstop only catches what the resident actually says. If the check-in agent skips
the required question, nothing is said and nothing is caught, so a deterministic check turns an
unestablished OK into UNCLEAR, which the state machine retries.
"""

import asyncio
from pathlib import Path

import pytest

from doorstep_agent.agents.classifier import classify_attempt, unanswered_required
from doorstep_agent.models import CheckinAttempt, CheckinStatus, ConversationTurn
from doorstep_agent.profiles import load_profile
from doorstep_agent.runtime import RunContext

DUMMY = Path(__file__).resolve().parent / "fixtures" / "profiles" / "dummy.yaml"


def _attempt(answers: dict[str, str], resident_says: str = "I'm fine, thanks") -> CheckinAttempt:
    return CheckinAttempt(
        attempt=1,
        channel="simulated",
        answered=True,
        answers=answers,
        transcript=[ConversationTurn(speaker="resident", text=resident_says)],
    )


def test_the_shared_feeling_question_is_the_required_one() -> None:
    assert load_profile("heat").required_question_ids() == ["feeling"]
    # A profile that marks nothing required imposes no protocol check.
    assert load_profile(DUMMY).required_question_ids() == []


def test_unanswered_required_spots_missing_and_blank_answers(ctx: RunContext) -> None:
    assert unanswered_required(ctx, _attempt({"feeling": "a bit tired"})) == []
    assert unanswered_required(ctx, _attempt({"supplies": "has water"})) == ["feeling"]
    assert unanswered_required(ctx, _attempt({"feeling": "   "})) == ["feeling"]


def test_skipped_question_turns_ok_into_unclear(ctx: RunContext, monkeypatch) -> None:
    """Walter's case: the agent never asked how he felt, so OK was never established."""
    monkeypatch.setattr(
        "doorstep_agent.agents.classifier._classify_with_model",
        _stub(CheckinStatus.OK, "Walter says the AC is on and he has water."),
    )
    resident = ctx.store.resident("r02")
    result = asyncio.run(classify_attempt(ctx, resident, _attempt({"supplies": "water is fine"})))
    assert result.status == CheckinStatus.UNCLEAR
    assert "Protocol incomplete" in result.summary
    note = next(e for e in ctx.audit.events() if e.actor == "system:protocol")
    assert note.data["missing"] == ["feeling"] and note.resident_id == "r02"


def test_a_complete_call_is_left_alone(ctx: RunContext, monkeypatch) -> None:
    monkeypatch.setattr(
        "doorstep_agent.agents.classifier._classify_with_model",
        _stub(CheckinStatus.OK, "Fine, AC on, has water."),
    )
    resident = ctx.store.resident("r06")
    result = asyncio.run(classify_attempt(ctx, resident, _attempt({"feeling": "fine"})))
    assert result.status == CheckinStatus.OK
    assert not [e for e in ctx.audit.events() if e.actor == "system:protocol"]


@pytest.mark.parametrize("status", [CheckinStatus.NEEDS_HELP, CheckinStatus.URGENT])
def test_the_check_never_touches_a_real_signal(ctx: RunContext, monkeypatch, status) -> None:
    """An incomplete call that still found a need or a red flag keeps that status."""
    monkeypatch.setattr("doorstep_agent.agents.classifier._classify_with_model", _stub(status, "x"))
    resident = ctx.store.resident("r03")
    result = asyncio.run(classify_attempt(ctx, resident, _attempt({})))
    assert result.status == status


def _stub(status: CheckinStatus, summary: str):
    from doorstep_agent.models import CheckinResult

    async def _fake(ctx, resident, attempt):  # noqa: ANN001
        return CheckinResult(status=status, language=resident.language, summary=summary)

    return _fake
