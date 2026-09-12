"""Hazard profile loader tests (SPEC §3a), including the Gate 1 dummy-profile check."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from doorstep_agent.profiles import HazardProfile, available_profiles, load_profile

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "profiles"


@pytest.fixture(scope="module")
def heat() -> HazardProfile:
    return load_profile("heat")


@pytest.fixture(scope="module")
def dummy() -> HazardProfile:
    return load_profile(FIXTURES / "dummy.yaml")


def test_heat_is_the_only_shipped_profile_for_now() -> None:
    assert "heat" in available_profiles()
    assert not any(p.startswith("_") for p in available_profiles())


def test_heat_merges_shared_parts(heat: HazardProfile) -> None:
    # Shared question first, then the profile's own three (SPEC §9 steps 2-5).
    assert [q.id for q in heat.checkin_questions] == [
        "feeling",
        "indoor_conditions",
        "supplies",
        "help_offer",
    ]
    # Shared red-flag categories (SPEC §6.1) plus the two heat-specific ones.
    assert heat.red_flag_categories() == [
        "confusion",
        "dizziness_fainting",
        "chest_pain",
        "trouble_breathing",
        "cannot_get_up",
        "other",
        "not_sweating_hot",
        "heat_indoors_extreme",
    ]
    assert heat.need_ids() == [
        "water",
        "ride",
        "medication_access",
        "food",
        "company",
        "power",
        "cooling",
    ]
    assert heat.relief_centre_kind == "cooling"
    assert "{first_name}" in heat.common.greeting_en
    assert len(heat.source_files) == 2 and heat.source_files[0].endswith("_shared.yaml")


def test_heat_activation_rules(heat: HazardProfile) -> None:
    assert heat.activation_for("Excessive Heat Warning") == "auto"
    assert heat.activation_for("Extreme Heat Warning") == "auto"
    assert heat.activation_for("Heat Advisory") == "ask"
    assert heat.activation_for("Excessive Heat Watch") == "ask"
    assert heat.activation_for("Dense Fog Advisory") is None
    # VTEC lookup works even when the event name is unknown (e.g. renamed products).
    assert heat.activation_for("Some Future Name", vtec="EH.W") == "auto"


def test_heat_risk_weights_match_spec(heat: HazardProfile) -> None:
    weights = {f.id: f.points for f in heat.risk_factors}
    assert weights == {
        "age_80_plus": 3,
        "age_70s": 2,
        "lives_alone": 3,
        "no_ac": 3,
        "power_dependent": 3,
        "mobility_limited": 2,
        "chronic_flag": 1,
        "prior_no_answer": 1,
    }
    assert (heat.wave_thresholds.wave1_min, heat.wave_thresholds.wave2_min) == (8, 5)


def test_every_red_flag_rule_has_english_and_spanish_phrases(heat: HazardProfile) -> None:
    for rule in heat.red_flags:
        assert rule.en, rule.category
        assert rule.es, rule.category


def test_language_helpers(heat: HazardProfile) -> None:
    assert heat.questions_text("es")[0] == "¿Cómo se siente ahora mismo?"
    assert heat.tip("en").startswith("Keep drinking water")
    assert heat.hazard_phrase("es") == "una alerta de calor extremo"
    assert heat.common_text("voicemail", "en").startswith("This is the {org_name}")


# --- Gate 1: a minimal dummy profile changes behaviour with no code changes -------------------


def test_dummy_profile_loads_without_shared_parts(dummy: HazardProfile) -> None:
    assert dummy.id == "dummy"
    assert dummy.include_shared is False
    assert len(dummy.source_files) == 1


def test_dummy_profile_changes_questions(dummy: HazardProfile, heat: HazardProfile) -> None:
    assert dummy.questions_text("en") == [
        "What colour is the smoke outside your window?",
        "Is your boat ready?",
    ]
    assert not set(dummy.questions_text("en")) & set(heat.questions_text("en"))


def test_dummy_profile_changes_risk_weights(dummy: HazardProfile) -> None:
    assert [(f.field, f.equals, f.points) for f in dummy.risk_factors] == [
        ("has_boat", True, 5),
        ("lives_alone", True, 1),
    ]
    assert (dummy.wave_thresholds.wave1_min, dummy.wave_thresholds.wave2_min) == (5, 3)


def test_dummy_profile_changes_red_flags_needs_and_relief_kind(dummy: HazardProfile) -> None:
    assert dummy.red_flag_categories() == ["purple_smoke_inside"]
    assert dummy.red_flags[0].phrases() == [
        "purple smoke inside",
        "purple haze",
        "humo morado adentro",
    ]
    assert dummy.need_ids() == ["sandbags"]
    assert dummy.relief_centre_kind == "shelter"
    assert dummy.activation_for("Purple Smoke Warning") == "auto"
    assert dummy.activation_for("Excessive Heat Warning") is None
    assert dummy.tip("en") == "Close the windows and count your sandbags."


# --- validation -------------------------------------------------------------------------------


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text((FIXTURES / "dummy.yaml").read_text() + "\nunexpected_key: 1\n")
    with pytest.raises(ValidationError):
        load_profile(bad)


def test_profile_without_shared_must_define_common(tmp_path: Path) -> None:
    text = (FIXTURES / "dummy.yaml").read_text()
    start = text.index("common:")
    end = text.index("alert_events:")
    (tmp_path / "nocommon.yaml").write_text(text[:start] + text[end:])
    with pytest.raises(ValueError, match="must define `common`"):
        load_profile(tmp_path / "nocommon.yaml")


def test_missing_profile_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_profile("volcano")
