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
    interleave with another, and `ReturnValues` describes that request's own write. moto has no
    lock, so two threads can both create a new counter at 1 or both win a claim. Serializing each
    request fixes the fake, not our code: the store's own threads, identity map and conditional
    writes still race each other for real.

    The lock covers moto's whole request handler, not just the backend call. `update_item` returns
    moto's live stored item and the handler reads `UPDATED_NEW` from it afterwards, so with only
    the backend locked a concurrent `ADD` could land in between and two threads read back the same
    counter (CI, 2026-09-13; reproduced 5/5 by pausing inside `Item.to_json`). The lock is
    re-entrant because the handler calls the backend methods it also wraps.
    """
    from moto.dynamodb.models import DynamoDBBackend
    from moto.dynamodb.responses import DynamoHandler

    lock = threading.RLock()
    backend_names = ("put_item", "get_item", "query", "scan", "update_item", "delete_item",
                     "transact_write_items")  # fmt: skip
    handler_names = (*backend_names, "batch_write_item", "batch_get_item")
    originals = [(DynamoDBBackend, n, getattr(DynamoDBBackend, n)) for n in backend_names]
    originals += [(DynamoHandler, n, getattr(DynamoHandler, n)) for n in handler_names]

    def serialized(method: Callable) -> Callable:
        @functools.wraps(method)
        def call(*args, **kwargs):
            with lock:
                return method(*args, **kwargs)

        return call

    for cls, name, method in originals:
        setattr(cls, name, serialized(method))
    yield
    for cls, name, method in originals:
        setattr(cls, name, method)


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
