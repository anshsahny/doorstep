"""Shared fixtures: a drill RunContext over the 12-resident subset, with cases created."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from doorstep_agent.audit import AuditLog
from doorstep_agent.config import settings
from doorstep_agent.models import Alert, Incident, ResidentCase
from doorstep_agent.profiles import load_profile
from doorstep_agent.risk import score_all
from doorstep_agent.runtime import RunContext
from doorstep_agent.state_machine import CasePolicy, Clock
from doorstep_agent.store import InMemoryStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ALERT_FIXTURE = DATA / "alerts" / "2021-06-pqr-excessive-heat-warning.json"


def make_ctx(
    mode: str = "drill",
    *,
    auto_approve: bool = True,
    profile_id: str = "heat",
    sessions_dir: Path | None = None,
) -> RunContext:
    roster = json.loads((DATA / "roster.json").read_text())
    store = InMemoryStore.from_data_dir(DATA, resident_ids=roster["drill_subset"])
    profile = load_profile(profile_id)
    clock = Clock(compression=30)
    incident_id = "inc-test"
    alert = Alert.from_fixture(json.loads(ALERT_FIXTURE.read_text()))
    store.save_incident(
        Incident(
            id=incident_id, org_id=store.org().id, mode=mode, profile_id=profile.id, alert=alert
        )
    )
    for rid, score in score_all(store.residents(), profile).items():
        store.save_case(ResidentCase(incident_id=incident_id, resident_id=rid, risk=score))
    cfg = settings()
    if sessions_dir is not None:
        # Paused sessions must land in the test's own tmp dir, never in the repo's .sessions/.
        cfg = replace(cfg, sessions_dir=sessions_dir)
    return RunContext(
        store=store,
        incident_id=incident_id,
        profile=profile,
        org=store.org(),
        mode=mode,  # type: ignore[arg-type]
        clock=clock,
        policy=CasePolicy(clock, max_attempts=3, retry_interval_minutes=10),
        audit=AuditLog(store, incident_id),
        settings=cfg,
        alert_severity=alert.severity,
        auto_approve=auto_approve,
    )


@pytest.fixture
def ctx_factory() -> Callable[..., RunContext]:
    return make_ctx


@pytest.fixture
def ctx() -> RunContext:
    return make_ctx()
