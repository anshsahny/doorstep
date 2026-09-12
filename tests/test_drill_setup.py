"""Drill runner setup, with no model calls: personas, roster subset and the incident record."""

from pathlib import Path

import pytest

from doorstep_agent.agents.persona import load_personas
from doorstep_agent.drill import DrillRunner
from doorstep_agent.models import CaseState

DUMMY = Path(__file__).resolve().parent / "fixtures" / "profiles" / "dummy.yaml"


def test_heat_drill_uses_the_twelve_personas() -> None:
    runner = DrillRunner(show_board=False)
    ctx = runner._build_context()
    assert sorted(runner.personas) == sorted(r.id for r in ctx.store.residents())
    assert len(runner.personas) == 12
    incident = ctx.store.incident(ctx.incident_id)
    assert incident.mode == "drill" and incident.status == "assessing"
    assert incident.alert.event == "Excessive Heat Warning"
    assert ctx.store.cases(ctx.incident_id) == []  # the outreach node creates them


def test_profile_without_personas_still_builds_on_the_drill_subset() -> None:
    runner = DrillRunner(profile_id=str(DUMMY), show_board=False)
    ctx = runner._build_context()
    assert runner.personas == {}
    assert len(ctx.store.residents()) == 12
    assert ctx.profile.id == "dummy"
    report = runner._report(activated=False)
    assert report.cases == [] and report.gate_passed() is False
    assert report.to_json().endswith("}\n")  # newline-terminated, so pre-commit leaves it alone


def test_report_marks_unexpected_residents_without_a_persona() -> None:
    runner = DrillRunner(profile_id=str(DUMMY), show_board=False)
    ctx = runner._build_context()
    from doorstep_agent.models import ResidentCase, RiskScore

    ctx.store.save_case(
        ResidentCase(
            incident_id=ctx.incident_id,
            resident_id="r01",
            risk=RiskScore(resident_id="r01", points=1, wave=3),
        )
    )
    row = runner._report(activated=True).cases[0]
    assert row["expected"] is None and row["state"] == CaseState.QUEUED


def test_load_personas_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_personas(tmp_path / "nope")
