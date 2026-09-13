"""One contract, two stores: what Doorstep's code relies on from any repository.

Every test here runs against `InMemoryStore` and against `DynamoStore` on moto, so the cloud
store is held to exactly the behaviour the Phase 1–2 code was written against — including the
parts in-memory storage gave for free (shared records) and the parts it only got right by luck
(unique ids and answered-once under threads).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from conftest import SUBSET, make_ctx, new_backend
from doorstep_agent.decisions import redrive_unapplied, upsert_decision
from doorstep_agent.models import (
    CaseState,
    CheckinResult,
    CheckinStatus,
    DecisionOption,
    OutboundMessage,
)
from doorstep_agent.notify.base import RecordingNotifier, deliver_pending
from doorstep_agent.runtime import RunContext, StoreOutbox
from doorstep_agent.state_machine import transition
from doorstep_agent.store import NotFound
from doorstep_agent.store_dynamo import StaleWrite


@pytest.fixture(params=["memory", "dynamo"])
def ctx(request: pytest.FixtureRequest) -> RunContext:
    if request.param == "memory":
        return make_ctx(auto_approve=False)
    request.getfixturevalue("aws")
    store = new_backend().for_incident("inc-test", SUBSET)
    return make_ctx(auto_approve=False, store=store)


def urgent_decision(ctx: RunContext, resident_id: str = "r01", tool_use_id: str = "tu-1"):
    case = ctx.store.case(ctx.incident_id, resident_id)
    if case.state == CaseState.QUEUED:
        ctx.policy.start_attempt(case, "simulated")
        ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.URGENT))
        transition(case, CaseState.ESCALATED, reason="test")
        ctx.store.save_case(case)
    decision = upsert_decision(
        ctx,
        tool_use_id=tool_use_id,
        resident_id=resident_id,
        name="doorstep-urgent-red-flag",
        reason="test",
        options=[
            DecisionOption(
                id="handle",
                label="I'm handling it",
                action="resolve",
                args={"resident_id": resident_id, "outcome": "captain handling"},
            )
        ],
        audience="cap-maria",
    )
    decision.status = "pending"
    decision.expires_at = ctx.clock.now() + timedelta(minutes=15)
    ctx.store.save_decision(decision)
    return decision


# --- the contract ---------------------------------------------------------------------------


def test_static_data_is_the_drill_subset(ctx: RunContext) -> None:
    assert ctx.store.org().id == "juniper-court"
    assert sorted(r.id for r in ctx.store.residents()) == sorted(SUBSET)
    assert ctx.store.volunteer("cap-maria").role == "captain"
    assert len(ctx.store.relief_centres()) == 5
    with pytest.raises(NotFound):
        ctx.store.resident("r48")  # on the roster, not in this drill


def test_a_held_record_sees_changes_made_through_the_store(ctx: RunContext) -> None:
    """`drill.py` holds a case across a dispatch and saves it afterwards; that must not undo it."""
    held = ctx.store.case(ctx.incident_id, "r01")
    ctx.policy.start_attempt(held, "simulated")
    ctx.store.save_case(held)

    inside_the_tool = ctx.store.case(ctx.incident_id, "r01")
    ctx.policy.apply_result(inside_the_tool, CheckinResult(status=CheckinStatus.OK))
    transition(inside_the_tool, CaseState.RESOLVED, reason="closed by the dispatcher")
    ctx.store.save_case(inside_the_tool)

    ctx.store.save_case(held)  # the runner's save after dispatch
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.RESOLVED


@pytest.fixture
def tight_switching() -> Iterator[None]:
    """Switch threads almost every bytecode, so races show up on every run, not just on a slow CI
    runner. Found two that way on 2026-09-13: moto's unlocked writes, and botocore's lazily built
    exception classes letting a lost conditional write escape as a crash."""
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(before)


@pytest.mark.usefixtures("tight_switching")
def test_ids_are_unique_under_threads(ctx: RunContext) -> None:
    with ThreadPoolExecutor(12) as pool:
        seqs = list(pool.map(lambda _: ctx.store.next_seq(ctx.incident_id), range(60)))
        decs = list(pool.map(lambda _: ctx.store.allocate(ctx.incident_id, "dec"), range(60)))
    assert len(set(seqs)) == 60
    assert len(set(decs)) == 60


@pytest.mark.usefixtures("tight_switching")
def test_concurrent_tool_bodies_never_share_a_decision_id(ctx: RunContext) -> None:
    """The Phase 2 counter was `len(decisions)+1`; Strands runs tool bodies in threads."""
    with ThreadPoolExecutor(8) as pool:
        made = list(
            pool.map(
                lambda i: (
                    upsert_decision(
                        ctx,
                        tool_use_id=f"tu-{i}",
                        resident_id="r01",
                        name="doorstep-unmet-need",
                        reason="x",
                        options=[],
                        audience="cap-maria",
                    ).id
                ),
                range(16),
            )
        )
    assert len(set(made)) == 16
    assert len(ctx.store.decisions(ctx.incident_id)) == 16


@pytest.mark.usefixtures("tight_switching")
def test_exactly_one_of_many_racing_claims_wins(ctx: RunContext) -> None:
    decision = urgent_decision(ctx)
    now = ctx.clock.now()
    with ThreadPoolExecutor(10) as pool:
        wins = list(
            pool.map(
                lambda i: ctx.store.claim_decision(
                    decision.id, responder=f"captain:{i}", response="handle", responded_at=now
                ),
                range(20),
            )
        )
    winners = [w for w in wins if w is not None]
    assert len(winners) == 1
    assert ctx.store.decision(decision.id).status == "answered"
    assert ctx.store.decision(decision.id).responder == winners[0].responder


@pytest.mark.usefixtures("tight_switching")
def test_a_claim_key_is_granted_once(ctx: RunContext) -> None:
    with ThreadPoolExecutor(8) as pool:
        granted = list(pool.map(lambda _: ctx.store.claim("DELIVERY#x"), range(16)))
    assert granted.count(True) == 1


def test_decisions_are_found_by_the_tool_use_that_raised_them(ctx: RunContext) -> None:
    decision = urgent_decision(ctx, tool_use_id="tu-find-me")
    assert ctx.store.decision_for_tool_use(ctx.incident_id, "tu-find-me").id == decision.id
    assert ctx.store.decision_for_tool_use(ctx.incident_id, "tu-other") is None
    assert [d.id for d in ctx.store.decisions(ctx.incident_id, status="pending")] == [decision.id]


def test_events_come_back_in_order_and_since(ctx: RunContext) -> None:
    for word in ("one", "two", "three"):
        ctx.audit.record(actor="system:test", type="note", reason=word)
    events = ctx.store.events(ctx.incident_id)
    assert [e.reason for e in events] == ["one", "two", "three"]
    assert [e.reason for e in ctx.store.events(ctx.incident_id, since_seq=events[0].seq)] == [
        "two",
        "three",
    ]


def test_the_outbox_is_kept_by_the_store(ctx: RunContext) -> None:
    outbox = StoreOutbox(ctx.store, ctx.incident_id)
    outbox.append(OutboundMessage(kind="resident_tip", recipient="r01", text="drink water"))
    assert [m.text for m in StoreOutbox(ctx.store, ctx.incident_id)] == ["drink water"]
    assert outbox == [OutboundMessage(kind="resident_tip", recipient="r01", text="drink water")]


def test_a_pending_decision_is_delivered_once(ctx: RunContext) -> None:
    urgent_decision(ctx)
    notifier = RecordingNotifier()
    assert deliver_pending(ctx, notifier) == 1
    assert deliver_pending(ctx, notifier) == 0, "a loop tick must not deliver it again"
    assert deliver_pending(ctx, RecordingNotifier()) == 0, "nor another notifier"
    assert len(notifier.decisions) == 1


def test_a_claimed_but_unapplied_decision_is_carried_out_once(ctx: RunContext) -> None:
    """A process that died between the claim and the effect leaves work for the next one."""
    decision = urgent_decision(ctx)
    ctx.store.claim_decision(
        decision.id,
        responder="captain:cap-maria",
        response="handle",
        responded_at=ctx.clock.now() - timedelta(minutes=5),
    )
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.ESCALATED

    assert len(redrive_unapplied(ctx)) == 1
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.RESOLVED
    assert redrive_unapplied(ctx) == [], "carried out once"


# --- DynamoDB only: what a second process sees ----------------------------------------------


def test_a_second_process_reads_what_the_first_saved(aws: None) -> None:
    first = make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    decision = urgent_decision(first)

    second = new_backend().for_incident("inc-test", SUBSET)
    assert second.decision(decision.id).status == "pending"
    assert second.case("inc-test", "r01").state == CaseState.ESCALATED
    assert second.incident("inc-test").resident_ids == SUBSET


def test_a_stale_write_from_another_process_fails_loudly(aws: None) -> None:
    first = make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    second = new_backend().for_incident("inc-test", SUBSET)
    theirs = second.case("inc-test", "r01")

    mine = first.store.case("inc-test", "r01")
    first.policy.start_attempt(mine, "simulated")
    first.store.save_case(mine)

    first.policy.start_attempt(theirs, "simulated")
    with pytest.raises(StaleWrite):
        second.save_case(theirs)


def test_two_processes_racing_one_decision_have_one_winner(aws: None) -> None:
    first = make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    decision = urgent_decision(first)
    stores = [new_backend().for_incident("inc-test", SUBSET) for _ in range(6)]
    now = first.clock.now()
    with ThreadPoolExecutor(6) as pool:
        wins = list(
            pool.map(
                lambda s: s.claim_decision(
                    decision.id, responder="captain:cap-maria", response="handle", responded_at=now
                ),
                stores,
            )
        )
    assert sum(w is not None for w in wins) == 1


def test_recreating_an_existing_incident_is_refused(aws: None) -> None:
    """A replayed start event must not silently reset an incident that is already running."""
    make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
    with pytest.raises(StaleWrite):
        make_ctx(store=new_backend().for_incident("inc-test", SUBSET))
