"""Shared fixtures: a drill RunContext over the 12-resident subset, with cases created."""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from doorstep_agent.audit import AuditLog
from doorstep_agent.config import settings
from doorstep_agent.models import Alert, Incident, ResidentCase
from doorstep_agent.profiles import load_profile
from doorstep_agent.risk import score_all
from doorstep_agent.runtime import RunContext
from doorstep_agent.state_machine import CasePolicy, Clock
from doorstep_agent.store import InMemoryStore, Repository
from doorstep_agent.store_dynamo import DynamoBackend, create_table

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ALERT_FIXTURE = DATA / "alerts" / "2021-06-pqr-excessive-heat-warning.json"

TABLE = "doorstep-test"
SUBSET: list[str] = json.loads((DATA / "roster.json").read_text())["drill_subset"]


@pytest.fixture(autouse=True, scope="session")
def atomic_moto_dynamodb() -> Iterator[None]:
    """Give moto real DynamoDB's per-request atomicity.

    Real DynamoDB applies each request atomically: an `ADD` counter or a conditional put cannot
    interleave with another. moto's in-memory backend reads an item, copies it and writes it back
    with no lock, so two threads can both create a new counter at 1 or both win a claim. That made
    the threaded store tests flaky on slower CI runners (reproduced 10/10 locally with
    `sys.setswitchinterval(1e-6)`). Serializing each backend request fixes the fake, not our code:
    the store's own threads, identity map and conditional writes still race each other for real.
    """
    from moto.dynamodb.models import DynamoDBBackend

    lock = threading.RLock()
    names = ("put_item", "get_item", "query", "scan", "update_item", "delete_item",
             "transact_write_items")  # fmt: skip
    originals = {name: getattr(DynamoDBBackend, name) for name in names}

    def serialized(method: Callable) -> Callable:
        @functools.wraps(method)
        def call(*args, **kwargs):
            with lock:
                return method(*args, **kwargs)

        return call

    for name, method in originals.items():
        setattr(DynamoDBBackend, name, serialized(method))
    yield
    for name, method in originals.items():
        setattr(DynamoDBBackend, name, method)


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("AWS_PROFILE", "AWS_ENDPOINT_URL_DYNAMODB", "AWS_ENDPOINT_URL_S3"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_table(client, TABLE)
        DynamoBackend(TABLE, "juniper-court", client=client).seed_static(DATA)
        yield


def new_backend() -> DynamoBackend:
    """A second backend is a second process: its own identity map, the same table."""
    return DynamoBackend(TABLE, "juniper-court", client=boto3.client("dynamodb"))


def make_ctx(
    mode: str = "drill",
    *,
    auto_approve: bool = True,
    profile_id: str = "heat",
    sessions_dir: Path | None = None,
    store: Repository | None = None,
    create_cases: bool = True,
) -> RunContext:
    """A drill context over the 12-resident subset. Pass `store` to run on another backend."""
    roster = json.loads((DATA / "roster.json").read_text())
    store = store or InMemoryStore.from_data_dir(DATA, resident_ids=roster["drill_subset"])
    profile = load_profile(profile_id)
    clock = Clock(compression=30)
    incident_id = "inc-test"
    alert = Alert.from_fixture(json.loads(ALERT_FIXTURE.read_text()))
    if create_cases:
        store.save_incident(
            Incident(
                id=incident_id,
                org_id=store.org().id,
                mode=mode,
                profile_id=profile.id,
                alert=alert,
                resident_ids=roster["drill_subset"],
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
