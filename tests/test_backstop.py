"""The deterministic red-flag backstop can only raise severity, and logs every disagreement."""

import random
from pathlib import Path

import pytest

from doorstep_agent.backstop import apply_backstop, find_red_flags, normalize
from doorstep_agent.models import CheckinResult, CheckinStatus
from doorstep_agent.profiles import load_profile

DUMMY = Path(__file__).resolve().parent / "fixtures" / "profiles" / "dummy.yaml"


@pytest.fixture(scope="module")
def heat():
    return load_profile("heat")


def _draft(status: CheckinStatus, red_flags: list[str] | None = None) -> CheckinResult:
    return CheckinResult(status=status, red_flags=red_flags or [], summary="model summary")


def test_raises_ok_to_urgent_on_a_red_flag_phrase(heat) -> None:
    out = apply_backstop(_draft(CheckinStatus.OK), "I'm fine, just a bit dizzy when I stand", heat)
    assert out.status == CheckinStatus.URGENT
    assert out.red_flags == ["dizziness_fainting"]
    assert out.backstop is not None
    assert out.backstop.raised is True
    assert out.backstop.disagreement == "backstop_raised"
    assert out.backstop.model_status == CheckinStatus.OK
    assert out.backstop.matched_phrases == ["dizzy"]


def test_hidden_red_flag_in_an_ok_sounding_sentence(heat) -> None:
    out = apply_backstop(
        _draft(CheckinStatus.OK), "Oh I'm fine, just not sure what day it is.", heat
    )
    assert out.status == CheckinStatus.URGENT
    assert "confusion" in out.red_flags


def test_never_lowers_urgent_and_keeps_model_flags(heat) -> None:
    draft = _draft(CheckinStatus.URGENT, ["chest_pain"])
    out = apply_backstop(draft, "I'm alright, thanks for calling", heat)
    assert out.status == CheckinStatus.URGENT
    assert out.red_flags == ["chest_pain"]
    assert out.backstop.raised is False
    assert out.backstop.disagreement == "model_urgent_no_match"


def test_agreement_is_logged_as_none(heat) -> None:
    out = apply_backstop(_draft(CheckinStatus.NEEDS_HELP), "Fan is on but I'm out of water", heat)
    assert out.status == CheckinStatus.NEEDS_HELP
    assert out.backstop.disagreement == "none" and out.backstop.raised is False


def test_model_flags_are_kept_and_backstop_categories_added(heat) -> None:
    draft = _draft(CheckinStatus.URGENT, ["other"])
    out = apply_backstop(draft, "my chest hurts and I feel faint", heat)
    assert out.red_flags == ["other", "dizziness_fainting", "chest_pain"]
    assert out.backstop.matched_categories == ["dizziness_fainting", "chest_pain"]
    assert out.backstop.disagreement == "none"


def test_spanish_phrases_and_accent_insensitivity(heat) -> None:
    assert normalize("No sé qué día es") == "no se que dia es"
    out = apply_backstop(_draft(CheckinStatus.OK), "Estoy bien pero no sé qué día es hoy.", heat)
    assert out.status == CheckinStatus.URGENT and "confusion" in out.red_flags
    out2 = apply_backstop(_draft(CheckinStatus.OK), "Me siento mareada y me duele el pecho", heat)
    assert set(out2.red_flags) == {"dizziness_fainting", "chest_pain"}


def test_heat_specific_categories_come_from_the_profile(heat) -> None:
    out = apply_backstop(
        _draft(CheckinStatus.OK), "It's like an oven in here and I stopped sweating", heat
    )
    assert set(out.red_flags) == {"heat_indoors_extreme", "not_sweating_hot"}


def test_single_word_negation_guard_and_multi_word_override(heat) -> None:
    assert find_red_flags("I'm not dizzy at all", heat) == []
    assert find_red_flags("no, never confused", heat) == []
    assert [m.category for m in find_red_flags("dizzy? not really", heat)] == ["dizziness_fainting"]
    # Multi-word phrases match even after a negation: "not sure what day" must never be suppressed.
    assert [m.category for m in find_red_flags("I'm not sure what day it is", heat)] == [
        "confusion"
    ]
    # The negation window is three words.
    assert find_red_flags("not at all dizzy", heat) == []
    assert [m.category for m in find_red_flags("not really all that dizzy", heat)] == [
        "dizziness_fainting"
    ]


def test_word_boundaries(heat) -> None:
    assert find_red_flags("the fainthearted cat", heat) == []
    assert find_red_flags("I fainted twice", heat)[0].category == "dizziness_fainting"


def test_agent_words_are_not_scanned_only_resident_text_is_passed() -> None:
    """The caller must pass resident turns only; an empty resident text never matches."""
    heat = load_profile("heat")
    assert find_red_flags("", heat) == []
    out = apply_backstop(_draft(CheckinStatus.NO_ANSWER), "", heat)
    assert out.status == CheckinStatus.NO_ANSWER and out.backstop.matched_phrases == []


def test_dummy_profile_changes_the_backstop_without_code_changes() -> None:
    dummy = load_profile(DUMMY)
    out = apply_backstop(_draft(CheckinStatus.OK), "I feel dizzy and confused", dummy)
    assert out.status == CheckinStatus.OK  # heat phrases mean nothing to the dummy profile
    out2 = apply_backstop(
        _draft(CheckinStatus.OK), "there is purple smoke inside my kitchen", dummy
    )
    assert out2.status == CheckinStatus.URGENT and out2.red_flags == ["purple_smoke_inside"]


SEVERITY = {
    CheckinStatus.OK: 0,
    CheckinStatus.NO_ANSWER: 0,
    CheckinStatus.UNCLEAR: 0,
    CheckinStatus.NEEDS_HELP: 1,
    CheckinStatus.URGENT: 2,
}
PHRASES = [
    "fine thanks",
    "a little dizzy",
    "chest pain since lunch",
    "not dizzy",
    "estoy en el suelo",
    "hot in here",
    "like an oven",
    "",
]


def test_property_never_lowers_never_drops_flags(heat) -> None:
    rng = random.Random(7)
    cats = heat.red_flag_categories()
    for _ in range(300):
        status = rng.choice(list(CheckinStatus))
        flags = rng.sample(cats, rng.randint(0, 3))
        text = " ".join(rng.choice(PHRASES) for _ in range(rng.randint(1, 3)))
        out = apply_backstop(_draft(status, list(flags)), text, heat)
        assert SEVERITY[out.status] >= SEVERITY[status]
        assert set(flags) <= set(out.red_flags)
        assert out.backstop is not None
        if out.backstop.matched_categories:
            assert out.status == CheckinStatus.URGENT
        else:
            assert out.status == status and out.red_flags == flags
        assert (out.backstop.disagreement == "backstop_raised") == (
            bool(out.backstop.matched_categories) and status != CheckinStatus.URGENT
        )
