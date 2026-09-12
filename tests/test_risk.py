"""Risk scoring and call-plan validation (SPEC §6.2), with the heat and dummy profiles."""

from pathlib import Path

import pytest

from doorstep_agent.models import CallPlan, CallWave, Resident, WaveAdjustment
from doorstep_agent.profiles import load_profile
from doorstep_agent.risk import (
    default_call_plan,
    score_all,
    score_resident,
    validate_call_plan,
    wave_for,
)
from doorstep_agent.store import InMemoryStore

DATA = Path(__file__).resolve().parents[1] / "data"
DUMMY = Path(__file__).resolve().parent / "fixtures" / "profiles" / "dummy.yaml"


def _resident(**overrides) -> Resident:
    base = dict(
        id="t1",
        name="Test Person",
        first_name="Test",
        building="x",
        address_label="x",
        lat=0.0,
        lng=0.0,
        age_band="60-69",
        lives_alone=False,
        has_ac=True,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        consent={"calls": True, "family": False, "share_with_volunteer": True},
    )
    base.update(overrides)
    return Resident.model_validate(base)


@pytest.fixture(scope="module")
def heat():
    return load_profile("heat")


@pytest.mark.parametrize(
    ("overrides", "expected_points", "expected_factors"),
    [
        ({}, 0, []),
        ({"age_band": "80+"}, 3, ["age_80_plus"]),
        ({"age_band": "70-79"}, 2, ["age_70s"]),
        ({"lives_alone": True}, 3, ["lives_alone"]),
        ({"has_ac": False}, 3, ["no_ac"]),
        ({"power_dependent": True}, 3, ["power_dependent"]),
        ({"mobility_limited": True}, 2, ["mobility_limited"]),
        ({"chronic_flag": True}, 1, ["chronic_flag"]),
        ({"prior_no_answer": True}, 1, ["prior_no_answer"]),
        (
            {"age_band": "80+", "lives_alone": True, "has_ac": False, "power_dependent": True},
            12,
            ["age_80_plus", "lives_alone", "no_ac", "power_dependent"],
        ),
    ],
)
def test_each_heat_factor_scores_per_spec(
    heat, overrides, expected_points, expected_factors
) -> None:
    score = score_resident(_resident(**overrides), heat)
    assert score.points == expected_points
    assert score.factors == expected_factors


def test_waves_from_thresholds(heat) -> None:
    assert wave_for(8, heat) == 1
    assert wave_for(12, heat) == 1
    assert wave_for(7, heat) == 2
    assert wave_for(5, heat) == 2
    assert wave_for(4, heat) == 3
    assert wave_for(0, heat) == 3


def test_drill_subset_scores(heat) -> None:
    store = InMemoryStore.from_data_dir(DATA)
    scores = score_all(store.residents(), heat)
    assert (
        scores["r01"].points == 10 and scores["r01"].wave == 1
    )  # Rose: 80+, alone, no AC, chronic
    assert scores["r05"].points == 13 and scores["r05"].wave == 1  # Harold: +power +prior no answer
    assert scores["r07"].points == 0 and scores["r07"].wave == 3  # Gloria: lives with family, AC
    assert scores["r09"].points == 6 and scores["r09"].wave == 2


def test_dummy_profile_changes_scoring_without_code_changes() -> None:
    dummy = load_profile(DUMMY)
    heat = load_profile("heat")
    boat_owner = _resident(lives_alone=True, has_ac=False, age_band="80+", extra={"has_boat": True})
    assert score_resident(boat_owner, heat).points == 9
    dummy_score = score_resident(boat_owner, dummy)
    assert dummy_score.points == 6
    assert dummy_score.factors == ["has_boat", "lives_alone"]
    assert dummy_score.wave == 1  # dummy wave1_min is 5
    no_boat = _resident(lives_alone=True)
    assert score_resident(no_boat, dummy).points == 1
    assert score_resident(no_boat, dummy).wave == 3


def test_default_call_plan_orders_by_wave_then_points(heat) -> None:
    scores = {
        "a": score_resident(
            _resident(id="a", age_band="80+", lives_alone=True, has_ac=False), heat
        ),
        "b": score_resident(_resident(id="b", age_band="70-79", lives_alone=True), heat),
        "c": score_resident(_resident(id="c"), heat),
        "d": score_resident(
            _resident(id="d", age_band="80+", lives_alone=True, has_ac=False, power_dependent=True),
            heat,
        ),
    }
    plan = default_call_plan(scores)
    assert [w.resident_ids for w in plan.waves] == [["d", "a"], ["b"], ["c"]]
    assert plan.ordered_ids() == ["d", "a", "b", "c"]


def test_validate_call_plan_allows_one_step_up_with_reason_and_blocks_everything_else(heat) -> None:
    scores = {
        "a": score_resident(
            _resident(id="a", age_band="80+", lives_alone=True, has_ac=False), heat
        ),  # 9 -> w1
        "b": score_resident(_resident(id="b", age_band="70-79", lives_alone=True), heat),  # 5 -> w2
        "c": score_resident(_resident(id="c"), heat),  # 0 -> w3
        "e": score_resident(_resident(id="e", chronic_flag=True), heat),  # 1 -> w3
    }
    proposed = CallPlan(
        waves=[
            CallWave(wave=1, resident_ids=["b", "c"]),  # b: up one with reason OK; c: jumped two
            CallWave(
                wave=2, resident_ids=["a", "e"]
            ),  # a: moved DOWN (blocked); e: up one, no reason
            CallWave(wave=3, resident_ids=["zz"]),  # not on roster
        ],
        adjustments=[
            WaveAdjustment(resident_id="b", from_wave=2, to_wave=1, reason="recently discharged"),
            WaveAdjustment(resident_id="c", from_wave=3, to_wave=1, reason="neighbour worried"),
        ],
    )
    plan, corrections = validate_call_plan(proposed, scores)
    by_wave = {w.wave: w.resident_ids for w in plan.waves}
    assert by_wave[1] == ["a", "b"]
    assert by_wave[2] == ["c"]  # jumped two with a reason: allowed one step only
    assert by_wave[3] == ["e"]
    assert {a.resident_id: (a.from_wave, a.to_wave) for a in plan.adjustments} == {
        "b": (2, 1),
        "c": (3, 2),
    }
    joined = "\n".join(corrections)
    assert "a: plan moved them down" in joined
    assert "e: moved up without a reason" in joined
    assert "zz: not on the roster" in joined
    assert "c: plan jumped" in joined


def test_validate_call_plan_restores_missing_and_duplicate_residents(heat) -> None:
    scores = {
        "a": score_resident(
            _resident(id="a", age_band="80+", lives_alone=True, has_ac=False), heat
        ),
        "b": score_resident(_resident(id="b"), heat),
    }
    proposed = CallPlan(waves=[CallWave(wave=1, resident_ids=["a", "a"])])
    plan, corrections = validate_call_plan(proposed, scores)
    assert plan.ordered_ids() == ["a", "b"]
    assert any("listed twice" in c for c in corrections)
    assert any("b: missing from the plan" in c for c in corrections)
