"""Synthetic data fixtures: shape, labelling and the no-PII rules (CLAUDE.md, SPEC §10)."""

import json
import re
from pathlib import Path

import pytest

from doorstep_agent.profiles import load_profile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

PHONE_LIKE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def roster() -> dict:
    return _load("roster.json")


def test_roster_has_48_fictional_residents_and_a_12_resident_drill_subset(roster: dict) -> None:
    residents = roster["residents"]
    assert len(residents) == 48
    assert roster["fictional"] is True
    assert all(r["fictional"] is True for r in residents)
    assert len(set(r["id"] for r in residents)) == 48
    ids = {r["id"] for r in residents}
    assert len(roster["drill_subset"]) == 12
    assert set(roster["drill_subset"]) <= ids


def test_roster_mix_matches_the_plan(roster: dict) -> None:
    residents = roster["residents"]
    assert sum(r["language"] == "es" for r in residents) == 6
    assert sum(r["interpreter_preference"] == "yue" for r in residents) == 2
    alone = sum(r["lives_alone"] for r in residents) / 48
    no_ac = sum(not r["has_ac"] for r in residents) / 48
    assert 0.30 <= alone <= 0.55, alone
    assert 0.25 <= no_ac <= 0.50, no_ac
    assert 2 <= sum(r["power_dependent"] for r in residents) <= 6


def test_roster_fields_follow_spec(roster: dict) -> None:
    required = {
        "id", "name", "first_name", "unit", "building", "address_label", "lat", "lng", "phone_ref",
        "language", "interpreter_preference", "age_band", "lives_alone", "has_ac",
        "power_dependent", "mobility_limited", "chronic_flag", "prior_no_answer", "consent",
        "family_contact_ref", "notes", "extra", "fictional",
    }  # fmt: skip
    for r in roster["residents"]:
        assert required <= set(r), r["id"]
        assert set(r["consent"]) == {"calls", "family", "share_with_volunteer"}
        assert r["age_band"] in {"60-69", "70-79", "80+"}
        assert r["language"] in {"en", "es"}
        if r["phone_ref"] is not None:
            assert r["phone_ref"].startswith("env:CALL_ALLOWLIST["), r["phone_ref"]
        if r["family_contact_ref"] is not None:
            assert r["family_contact_ref"].startswith("contact:"), r["family_contact_ref"]


def test_no_phone_numbers_or_chat_ids_in_data_files() -> None:
    for path in DATA.rglob("*.json"):
        if path.parent.name == "alerts":
            continue  # NWS product text legitimately contains numbers (dates, temperatures)
        text = path.read_text(encoding="utf-8")
        assert not PHONE_LIKE.search(text), f"phone-number-like string in {path.name}"


def test_volunteers_reference_env_vars_only() -> None:
    vols = _load("volunteers.json")["volunteers"]
    assert len(vols) == 7
    assert sum(v["role"] == "captain" for v in vols) == 1
    assert sum(v["role"] == "volunteer" for v in vols) == 6
    for v in vols:
        assert v["telegram_chat_id_ref"].startswith("env:TELEGRAM_"), v["id"]


def test_relief_centres_are_labelled_sample_and_tagged() -> None:
    doc = _load("relief_centres.json")
    assert "verify" in doc["verification_notice"]
    assert len(doc["centres"]) == 5
    for c in doc["centres"]:
        assert set(c["kind"]) <= {"cooling", "warming"}
        assert "cooling" in c["kind"]


def test_alert_fixture_is_real_archived_text_and_activates_heat() -> None:
    doc = _load("alerts/2021-06-pqr-excessive-heat-warning.json")
    assert "mesonet.agron.iastate.edu" in doc["source"]["json_url"]
    props = doc["features"][0]["properties"]
    assert props["event"] == "Excessive Heat Warning"
    assert doc["vtec"] == "/O.NEW.KPQR.EH.W.0001.210626T1700Z-210629T0600Z/"
    assert props["onset"] == "2021-06-26T17:00:00Z"
    assert "ORZ006" in props["geocode"]["UGC"]
    assert "Greater Portland Metro Area" in props["areaDesc"]
    assert load_profile("heat").activation_for(props["event"]) == "auto"


def test_org_is_fictional_with_a_real_zone() -> None:
    org = _load("org.json")
    assert org["fictional"] is True
    assert org["nws"]["zone"] == "ORZ006"
    assert org["timezone"] == "America/Los_Angeles"
    assert org["phone_tree_baseline"]["residents"] == 48
