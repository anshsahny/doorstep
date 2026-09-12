"""Minimal disclosure, held to the exact words that reach a phone (SPEC §4, §7).

The captain's message must carry enough to decide. The volunteer's must carry only what changes
the knock. These tests pin both, and then sweep the whole drill roster so the rule cannot be
true for the one resident someone happened to write a test for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import make_ctx
from doorstep_agent.decisions import upsert_decision
from doorstep_agent.messages import captain_message, volunteer_task
from doorstep_agent.models import CheckinResult, CheckinStatus, DecisionOption
from doorstep_agent.runtime import RunContext

QUOTE = "I'm so dizzy and I can't think straight"


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return make_ctx(auto_approve=False, sessions_dir=tmp_path / "sessions")


def with_result(ctx: RunContext, rid: str, status: CheckinStatus, **kw):
    case = ctx.store.case(ctx.incident_id, rid)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=status, **kw))
    ctx.store.save_case(case)
    return case


def a_decision(ctx: RunContext, rid: str, name: str, reason: str):
    return upsert_decision(
        ctx,
        tool_use_id=f"tu-{rid}-{name}",
        resident_id=rid,
        name=name,
        reason=reason,
        options=[DecisionOption(id="handle", label="I'm handling it", action="resolve")],
        audience="cap-maria",
    )


# --- the captain has enough to decide --------------------------------------------------------


def test_the_urgent_message_carries_what_the_captain_needs(ctx: RunContext) -> None:
    with_result(ctx, "r01", CheckinStatus.URGENT, red_flags=["dizziness_fainting"], key_quote=QUOTE)
    decision = a_decision(ctx, "r01", "doorstep-urgent-red-flag", "Said she is dizzy and confused.")

    text = captain_message(ctx, decision)

    assert "Rose" in text and "80+" in text and "lives alone" in text
    assert "Juniper Court, Unit 3C" in text
    assert QUOTE in text, "the captain decides on the resident's own words"
    assert "told to call 911" in text
    assert "Doorstep does not call emergency services" in text
    assert "fictional drill data" in text, "every screenshot must label itself"
    assert decision.id in text


def test_the_no_answer_message_says_why_this_resident_is_urgent(ctx: RunContext) -> None:
    with_result(ctx, "r05", CheckinStatus.NO_ANSWER)
    decision = a_decision(ctx, "r05", "doorstep-high-risk-no-answer", "No answer after 3 calls.")

    text = captain_message(ctx, decision)

    assert "Harold" in text and "Juniper Court, Unit 4D" in text
    assert "No answer after 3 calls" in text
    # The one health-adjacent fact Doorstep keeps, and the reason to hurry.
    assert "needs power for a medical device" in text
    assert "No AC" in text, "the acronym must survive sentence-casing"
    assert "Approve a door-knock?" in text


# --- the volunteer gets only what changes the knock -------------------------------------------


def test_the_volunteer_task_omits_the_residents_words(ctx: RunContext) -> None:
    case = with_result(
        ctx, "r01", CheckinStatus.URGENT, red_flags=["dizziness_fainting"], key_quote=QUOTE
    )
    resident = ctx.store.resident("r01")

    text = volunteer_task(
        ctx, resident, case, "The captain asked for someone now", volunteer_name="Tom"
    )

    assert "Tom" in text and "Rose" in text and "Juniper Court, Unit 3C" in text
    assert QUOTE not in text, "the most sensitive sentence of the call is not a door-knock detail"
    assert "dizzy" not in text.lower()
    # The note that changes how Tom knocks does belong.
    assert "hard of hearing" in text.lower()
    assert "call 911 yourself" in text


def test_a_consented_standing_note_does_reach_the_volunteer(ctx: RunContext) -> None:
    """The line is standing facts vs today's call, not health vs the rest.

    Harold's notes say he needs power for a medical device and often leaves the phone off the
    hook. Both change what Tom does at the door and when nobody answers, both are consented
    roster data, so both travel. His words on today's call would not.
    """
    case = with_result(ctx, "r05", CheckinStatus.NO_ANSWER, key_quote="something he said")
    resident = ctx.store.resident("r05")

    text = volunteer_task(ctx, resident, case, "No answer after three calls", volunteer_name="Tom")

    assert "needs power for a medical device" in text.lower()
    assert "leaves the phone off the hook" in text.lower()
    assert "something he said" not in text


def test_a_resident_who_did_not_consent_is_not_named(ctx: RunContext) -> None:
    resident = ctx.store.resident("r01").model_copy(deep=True)
    resident.consent.share_with_volunteer = False
    case = with_result(ctx, "r01", CheckinStatus.NEEDS_HELP, needs=["water"])

    text = volunteer_task(ctx, resident, case, "Needs water", volunteer_name="Tom")

    assert "Rose" not in text
    assert "the resident" in text
    assert "hard of hearing" not in text.lower(), "a note is a detail too"
    assert "Juniper Court, Unit 3C" in text, "the door is still needed"


# --- the rule holds for everyone, not just the resident someone tested ------------------------

# Things that must never appear. Note the absence of the word "phone": Harold's consented note
# "Often leaves the phone off the hook" is exactly the kind of detail a volunteer should have,
# and banning the word rather than the number would have deleted it.
FORBIDDEN_IN_VOLUNTEER_TASKS = ("risk", "points", "wave", "contact:", "env:", "+1")
DIGITS = re.compile(r"\d{4,}")  # a phone number or chat id; unit numbers are short


def test_no_volunteer_task_leaks_anything_across_the_whole_roster(ctx: RunContext) -> None:
    residents = ctx.store.residents()
    quotes = {r.id: f"{r.first_name} said something private about their health" for r in residents}

    for resident in residents:
        case = with_result(
            ctx,
            resident.id,
            CheckinStatus.NEEDS_HELP,
            needs=["water"],
            key_quote=quotes[resident.id],
        )
        text = volunteer_task(ctx, resident, case, "Needs a hand", volunteer_name="Tom")
        lowered = text.lower()

        assert quotes[resident.id] not in text, f"{resident.id}: quote leaked"
        for banned in FORBIDDEN_IN_VOLUNTEER_TASKS:
            assert banned not in lowered, f"{resident.id}: '{banned}' leaked"
        assert not DIGITS.search(text), f"{resident.id}: a number that long is contact data"
        if resident.family_contact_ref:
            assert resident.family_contact_ref not in text, f"{resident.id}: family ref leaked"
        if resident.phone_ref:
            assert resident.phone_ref not in text, f"{resident.id}: phone ref leaked"

        others = [o for o in residents if o.id != resident.id]
        for other in others:
            assert other.name not in text, f"{resident.id}: names {other.id}"
            assert other.address_label not in text, f"{resident.id}: gives {other.id}'s address"


def test_every_captain_message_is_labelled_fictional_in_a_drill(ctx: RunContext) -> None:
    for resident in ctx.store.residents():
        with_result(ctx, resident.id, CheckinStatus.NEEDS_HELP, needs=["water"])
        decision = a_decision(ctx, resident.id, "doorstep-unmet-need", "Nobody is free.")
        assert "fictional drill data" in captain_message(ctx, decision)
