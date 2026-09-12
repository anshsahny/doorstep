"""Domain models and the in-memory store load the synthetic data and round-trip records."""

import json
from pathlib import Path

import pytest

from doorstep_agent.config import settings
from doorstep_agent.models import (
    Alert,
    AuditEvent,
    CaseState,
    Decision,
    DecisionOption,
    Incident,
    Resident,
    ResidentCase,
    RiskScore,
)
from doorstep_agent.store import InMemoryStore, NotFound

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def store() -> InMemoryStore:
    return InMemoryStore.from_data_dir(DATA)


def test_store_loads_all_static_data(store: InMemoryStore) -> None:
    assert store.org().id == "juniper-court"
    assert len(store.residents()) == 48
    assert len(store.volunteers()) == 7
    assert len(store.relief_centres()) == 5
    assert store.resident("r01").first_name == "Rose"
    assert store.volunteer("cap-maria").role == "captain"


def test_store_can_restrict_to_the_drill_subset() -> None:
    roster = json.loads((DATA / "roster.json").read_text())
    sub = InMemoryStore.from_data_dir(DATA, resident_ids=roster["drill_subset"])
    assert len(sub.residents()) == 12
    with pytest.raises(NotFound):
        InMemoryStore.from_data_dir(DATA, resident_ids=["r99"])


def test_resident_field_value_reads_model_fields_then_extra() -> None:
    r = Resident.model_validate(
        {
            **json.loads((DATA / "roster.json").read_text())["residents"][0],
            "extra": {"has_heat": False},
        }
    )
    assert r.field_value("lives_alone") is True
    assert r.field_value("has_heat") is False
    assert r.field_value("no_such_field") is None


def test_alert_from_fixture() -> None:
    doc = json.loads((DATA / "alerts" / "2021-06-pqr-excessive-heat-warning.json").read_text())
    alert = Alert.from_fixture(doc)
    assert alert.event == "Excessive Heat Warning"
    assert alert.severity == "Severe"
    assert alert.vtec_code() == "EH.W"
    assert "ORZ006" in alert.ugc
    assert alert.onset.startswith("2021-06-26")


def test_alert_from_live_nws_feature_shape() -> None:
    feature = {
        "properties": {
            "id": "x",
            "event": "Extreme Heat Warning",
            "severity": "Extreme",
            "parameters": {"VTEC": ["/O.NEW.KPQR.XH.W.0003.250801T1900Z-250803T0500Z/"]},
            "geocode": {"UGC": ["ORZ006"]},
        }
    }
    alert = Alert.from_nws_feature(feature)
    assert alert.vtec_code() == "XH.W"
    assert alert.severity == "Extreme"
    assert (
        Alert.from_nws_feature(
            {"properties": {"id": "y", "event": "Heat Advisory", "severity": "Nope"}}
        ).severity
        == "Unknown"
    )


def test_incident_case_decision_and_events_round_trip(store: InMemoryStore) -> None:
    doc = json.loads((DATA / "alerts" / "2021-06-pqr-excessive-heat-warning.json").read_text())
    inc = Incident(
        id="inc-1",
        org_id="juniper-court",
        mode="drill",
        profile_id="heat",
        alert=Alert.from_fixture(doc),
    )
    store.save_incident(inc)
    assert store.incident("inc-1").mode == "drill"

    case = ResidentCase(
        incident_id="inc-1",
        resident_id="r01",
        risk=RiskScore(resident_id="r01", points=10, factors=["age_80_plus"], wave=1),
    )
    store.save_case(case)
    assert store.case("inc-1", "r01").state == CaseState.QUEUED
    assert [c.resident_id for c in store.cases("inc-1")] == ["r01"]

    decision = Decision(
        id="dec-1",
        incident_id="inc-1",
        resident_id="r01",
        name="doorstep-urgent-red-flag",
        reason="test",
        options=[DecisionOption(id="handle", label="I'm handling it", action="resolve")],
        tool_use_id="tu-1",
    )
    store.save_decision(decision)
    # A decision starts as a draft: raised, but not answerable until the runner has stamped the
    # interrupt on it, so nobody can be shown a button for it yet.
    assert store.decisions("inc-1", status="draft")[0].id == "dec-1"
    assert store.decisions("inc-1", status="pending") == []
    assert store.decisions("inc-1", status="answered") == []
    # The record is found again by the tool use that raised it, so a re-executed tool body
    # cannot create a second one.
    assert store.decision_for_tool_use("inc-1", "tu-1") is decision
    assert store.decision_for_tool_use("inc-1", "tu-other") is None

    assert store.next_seq("inc-1") == 1
    store.append_event(
        AuditEvent(seq=1, incident_id="inc-1", actor="system:test", type="note", reason="hi")
    )
    store.append_event(
        AuditEvent(seq=2, incident_id="inc-1", actor="system:test", type="note", reason="two")
    )
    assert [e.seq for e in store.events("inc-1")] == [1, 2]
    assert [e.seq for e in store.events("inc-1", since_seq=1)] == [2]
    assert store.next_seq("inc-1") == 3
    assert "hi" in store.events("inc-1")[0].line()

    with pytest.raises(NotFound):
        store.case("inc-1", "r99")
    with pytest.raises(NotFound):
        store.decision("nope")


def test_settings_defaults() -> None:
    s = settings()
    assert s.model_agent == "us.amazon.nova-2-lite-v1:0"
    assert s.model_persona == "us.amazon.nova-micro-v1:0"
    assert s.drill_time_compression == 30.0
    assert s.default_profile == "heat"
    assert s.default_alert_fixture.name == "2021-06-pqr-excessive-heat-warning.json"
