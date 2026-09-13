"""The DynamoDB repository (SPEC §10), for the cloud coordinator (PLAN Phase 3).

One table, `doorstep`:

    ORG#<org>        PROFILE | RES#<id> | VOL#<id> | CENTRE#<id>      static, written by `make seed`
    INC#<id>         META                                             the incident
                     CASE#<resident> | DEC#<decision>                 versioned records
                     EVT#<seq:08d> | MSG#<n:08d>                      append-only audit and outbox
                     COUNTER#<name>                                   atomic id counters
    CLAIM#<key>      CLAIM                                            one-time side effects (TTL)

Records are stored as their Pydantic JSON in `doc`, so nothing about the domain is re-described
here and floats never meet DynamoDB's Decimal.

Two things make the Phase 1–2 code run on this unchanged (see `store.py`):

* **An identity map per process.** Phase 1–2 code holds a record while an agent changes the same
  case, then saves its copy (`drill.py`, `process_case`). With a fresh copy per read that save
  would silently undo the agent. Here, as in memory, every reader in the process gets the same
  object for the same record.
* **Versioned writes.** Every CASE/DEC/META write is conditional on the version this process last
  saw. A second process writing the same incident is therefore a loud `StaleWrite`, never a lost
  update. The coordinator keeps one process per incident (one AgentCore session per incident), so
  in normal operation this never fires; it exists so that a broken assumption cannot pass quietly.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel

from .models import (
    AuditEvent,
    Decision,
    Incident,
    OrgProfile,
    OutboundMessage,
    ReliefCentre,
    Resident,
    ResidentCase,
    Volunteer,
)
from .store import NotFound


def conditional_failed(exc: ClientError) -> bool:
    """True if a DynamoDB write was refused by its condition.

    Matched on the error code, never on `client.exceptions.ConditionalCheckFailedException`:
    botocore builds that class lazily and without a lock, so two threads touching it first can
    each get their own class, and one thread's `except` then misses the other's exception (found
    in CI on 2026-09-13; reproduced with `sys.setswitchinterval(1e-6)`).
    """
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


CLAIM_TTL_SECONDS = 14 * 24 * 3600
_MAX_SEQ = "99999999"

M = TypeVar("M", bound=BaseModel)
Key = tuple[str, str]


class StaleWrite(RuntimeError):
    """Another writer changed this record since this process read it."""


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _n(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _refresh_in_place(target: BaseModel, source: BaseModel) -> None:
    for name in type(target).model_fields:
        setattr(target, name, getattr(source, name))


class DynamoBackend:
    """The table, plus this process's view of the records in it. One per process."""

    def __init__(
        self,
        table_name: str,
        org_id: str,
        *,
        client: Any = None,
        cache: bool = True,
    ) -> None:
        """`cache=False` re-reads on every call: for an operator watching a coordinator's work."""
        self.table_name = table_name
        self.org_id = org_id
        self.client = client or boto3.client("dynamodb")
        self.cache = cache
        self._lock = threading.RLock()
        self._objs: dict[Key, BaseModel] = {}
        self._ver: dict[Key, int] = {}
        self._loaded: set[str] = set()
        self._events: dict[str, list[AuditEvent]] = {}
        self._messages: dict[str, list[OutboundMessage]] = {}
        self._static: dict[str, Any] | None = None

    def for_incident(self, incident_id: str, resident_ids: list[str] | None = None) -> DynamoStore:
        return DynamoStore(self, incident_id, resident_ids)

    # --- low level ---

    def _query(self, pk: str, *, prefix: str = "", between: tuple[str, str] | None = None):
        kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "ConsistentRead": True,
            "ExpressionAttributeValues": {":pk": _s(pk)},
        }
        if between:
            kwargs["KeyConditionExpression"] = "PK = :pk AND SK BETWEEN :a AND :b"
            kwargs["ExpressionAttributeValues"].update({":a": _s(between[0]), ":b": _s(between[1])})
        elif prefix:
            kwargs["KeyConditionExpression"] = "PK = :pk AND begins_with(SK, :p)"
            kwargs["ExpressionAttributeValues"][":p"] = _s(prefix)
        else:
            kwargs["KeyConditionExpression"] = "PK = :pk"
        while True:
            page = self.client.query(**kwargs)
            yield from page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _adopt(self, key: Key, item: dict[str, Any], cls: type[M]) -> M:
        """Take a stored item into the identity map, refreshing a held object in place."""
        fresh = cls.model_validate_json(item["doc"]["S"])
        held = self._objs.get(key)
        if held is not None:
            _refresh_in_place(held, fresh)
            fresh = held  # type: ignore[assignment]
        self._objs[key] = fresh
        self._ver[key] = int(item.get("v", {"N": "0"})["N"])
        return fresh

    def _get(self, key: Key, cls: type[M]) -> M:
        with self._lock:
            if self.cache and key in self._objs:
                return self._objs[key]  # type: ignore[return-value]
            item = self.client.get_item(
                TableName=self.table_name,
                Key={"PK": _s(key[0]), "SK": _s(key[1])},
                ConsistentRead=True,
            ).get("Item")
            if not item:
                raise NotFound(f"{key[0]} {key[1]}")
            return self._adopt(key, item, cls)

    def _put_versioned(
        self, key: Key, model: BaseModel, extra: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            seen = self._ver.get(key)
            item = {
                "PK": _s(key[0]),
                "SK": _s(key[1]),
                "doc": _s(model.model_dump_json()),
                "v": _n((seen or 0) + 1),
                **(extra or {}),
            }
            kwargs: dict[str, Any] = {"TableName": self.table_name, "Item": item}
            if seen is None:
                kwargs["ConditionExpression"] = "attribute_not_exists(PK)"
            else:
                kwargs["ConditionExpression"] = "v = :v"
                kwargs["ExpressionAttributeValues"] = {":v": _n(seen)}
            try:
                self.client.put_item(**kwargs)
            except ClientError as exc:
                if not conditional_failed(exc):
                    raise
                raise StaleWrite(
                    f"{key[0]} {key[1]} was changed by another writer (this process saw "
                    f"version {seen})"
                ) from exc
            self._ver[key] = (seen or 0) + 1
            self._objs[key] = model

    # --- static org data ---

    def _static_data(self) -> dict[str, Any]:
        with self._lock:
            if self._static is None:
                data: dict[str, Any] = {"residents": {}, "volunteers": {}, "centres": []}
                for item in self._query(f"ORG#{self.org_id}"):
                    sk, doc = item["SK"]["S"], item["doc"]["S"]
                    if sk == "PROFILE":
                        data["org"] = OrgProfile.model_validate_json(doc)
                    elif sk.startswith("RES#"):
                        r = Resident.model_validate_json(doc)
                        data["residents"][r.id] = r
                    elif sk.startswith("VOL#"):
                        v = Volunteer.model_validate_json(doc)
                        data["volunteers"][v.id] = v
                    elif sk.startswith("CENTRE#"):
                        data["centres"].append(ReliefCentre.model_validate_json(doc))
                if "org" not in data:
                    raise NotFound(f"org {self.org_id} is not in {self.table_name}; run make seed")
                self._static = data
            return self._static

    def seed_static(self, data_dir: Path) -> int:
        """Write the org, roster, volunteers and relief centres from `data/`. Idempotent."""

        def read(name: str) -> dict[str, Any]:
            return json.loads((data_dir / name).read_text(encoding="utf-8"))

        org = OrgProfile.model_validate(read("org.json"))
        rows: list[tuple[str, BaseModel]] = [("PROFILE", org)]
        rows += [
            (f"RES#{r['id']}", Resident.model_validate(r)) for r in read("roster.json")["residents"]
        ]
        rows += [
            (f"VOL#{v['id']}", Volunteer.model_validate(v))
            for v in read("volunteers.json")["volunteers"]
        ]
        rows += [
            (f"CENTRE#{c['id']}", ReliefCentre.model_validate(c))
            for c in read("relief_centres.json")["centres"]
        ]
        pk = f"ORG#{org.id}"
        for start in range(0, len(rows), 25):
            requests = [
                {
                    "PutRequest": {
                        "Item": {"PK": _s(pk), "SK": _s(sk), "doc": _s(model.model_dump_json())}
                    }
                }
                for sk, model in rows[start : start + 25]
            ]
            pending: dict[str, Any] = {self.table_name: requests}
            while pending:
                pending = self.client.batch_write_item(RequestItems=pending).get(
                    "UnprocessedItems", {}
                )
        with self._lock:
            self._static = None
        return len(rows)

    def org(self) -> OrgProfile:
        return self._static_data()["org"]

    def residents(self) -> dict[str, Resident]:
        return self._static_data()["residents"]

    def volunteers(self) -> dict[str, Volunteer]:
        return self._static_data()["volunteers"]

    def relief_centres(self) -> list[ReliefCentre]:
        return list(self._static_data()["centres"])

    # --- incidents ---

    def _partition(self, incident_id: str) -> None:
        """Load an incident's cases and decisions once (every call, without the cache)."""
        with self._lock:
            if self.cache and incident_id in self._loaded:
                return
            pk = f"INC#{incident_id}"
            for prefix, cls in (("CASE#", ResidentCase), ("DEC#", Decision)):
                for item in self._query(pk, prefix=prefix):
                    key = (pk, item["SK"]["S"])
                    if self.cache and key in self._objs:
                        continue  # this process's copy is the live one
                    self._adopt(key, item, cls)
            self._loaded.add(incident_id)

    def _rows(self, incident_id: str, prefix: str) -> list[Any]:
        self._partition(incident_id)
        pk = f"INC#{incident_id}"
        with self._lock:
            return [o for (p, sk), o in self._objs.items() if p == pk and sk.startswith(prefix)]

    def save_incident(self, incident: Incident) -> None:
        extra = {}
        if incident.status == "active":
            extra = {
                "GSI1PK": _s("STATUS#active"),
                "GSI1SK": _s(f"{incident.started_at.isoformat()}#{incident.id}"),
            }
        self._put_versioned((f"INC#{incident.id}", "META"), incident, extra)

    def incident(self, incident_id: str) -> Incident:
        return self._get((f"INC#{incident_id}", "META"), Incident)

    def save_case(self, case: ResidentCase) -> None:
        self._partition(case.incident_id)
        self._put_versioned((f"INC#{case.incident_id}", f"CASE#{case.resident_id}"), case)

    def case(self, incident_id: str, resident_id: str) -> ResidentCase:
        self._partition(incident_id)
        found = self._objs.get((f"INC#{incident_id}", f"CASE#{resident_id}"))
        if found is None:
            raise NotFound(f"case {incident_id}/{resident_id}")
        return found  # type: ignore[return-value]

    def cases(self, incident_id: str) -> list[ResidentCase]:
        return self._rows(incident_id, "CASE#")

    def save_decision(self, decision: Decision) -> None:
        self._partition(decision.incident_id)
        self._put_versioned(
            (f"INC#{decision.incident_id}", f"DEC#{decision.id}"),
            decision,
            {"status": _s(decision.status)},
        )

    def decision(self, incident_id: str, decision_id: str) -> Decision:
        self._partition(incident_id)
        found = self._objs.get((f"INC#{incident_id}", f"DEC#{decision_id}"))
        if found is None:
            raise NotFound(f"decision {decision_id}")
        return found  # type: ignore[return-value]

    def decisions(self, incident_id: str, status: str | None = None) -> list[Decision]:
        return [d for d in self._rows(incident_id, "DEC#") if status is None or d.status == status]

    def claim_decision(
        self,
        incident_id: str,
        decision_id: str,
        *,
        responder: str,
        response: str,
        responded_at: datetime,
    ) -> Decision | None:
        """pending -> answered, conditional on the version: exactly one claimant wins."""
        key = (f"INC#{incident_id}", f"DEC#{decision_id}")
        for _ in range(3):
            with self._lock:
                held = self.decision(incident_id, decision_id)
                if held.status != "pending":
                    return None
                candidate = held.model_copy(deep=True)
                candidate.status = "answered"
                candidate.responder = responder
                candidate.response = response
                candidate.responded_at = responded_at
                try:
                    self._put_versioned(key, candidate, {"status": _s("answered")})
                except StaleWrite:
                    # Someone wrote this decision since we read it. Re-read: if it is still
                    # pending the change was unrelated (a delivery), so try again.
                    item = self.client.get_item(
                        TableName=self.table_name,
                        Key={"PK": _s(key[0]), "SK": _s(key[1])},
                        ConsistentRead=True,
                    )["Item"]
                    self._adopt(key, item, Decision)
                    continue
                # Keep the object every holder already has, now answered.
                self._objs[key] = held
                _refresh_in_place(held, candidate)
                return held
        return None

    def allocate(self, incident_id: str, counter: str) -> int:
        result = self.client.update_item(
            TableName=self.table_name,
            Key={"PK": _s(f"INC#{incident_id}"), "SK": _s(f"COUNTER#{counter}")},
            UpdateExpression="ADD n :one",
            ExpressionAttributeValues={":one": _n(1)},
            ReturnValues="UPDATED_NEW",
        )
        return int(result["Attributes"]["n"]["N"])

    def append_event(self, event: AuditEvent) -> None:
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "PK": _s(f"INC#{event.incident_id}"),
                "SK": _s(f"EVT#{event.seq:08d}"),
                "doc": _s(event.model_dump_json()),
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        with self._lock:
            if self.cache and event.incident_id in self._events:
                self._events[event.incident_id].append(event)

    def events(self, incident_id: str, since_seq: int = 0) -> list[AuditEvent]:
        with self._lock:
            if self.cache and incident_id in self._events:
                cached = self._events[incident_id]
                return sorted((e for e in cached if e.seq > since_seq), key=lambda e: e.seq)
        start = f"EVT#{since_seq + 1:08d}"
        found = [
            AuditEvent.model_validate_json(item["doc"]["S"])
            for item in self._query(f"INC#{incident_id}", between=(start, f"EVT#{_MAX_SEQ}"))
        ]
        if self.cache and since_seq == 0:
            with self._lock:
                self._events.setdefault(incident_id, found)
        return found

    def append_message(self, incident_id: str, message: OutboundMessage) -> None:
        n = self.allocate(incident_id, "msg")
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "PK": _s(f"INC#{incident_id}"),
                "SK": _s(f"MSG#{n:08d}"),
                "doc": _s(message.model_dump_json()),
            },
        )
        with self._lock:
            if self.cache and incident_id in self._messages:
                self._messages[incident_id].append(message)

    def messages(self, incident_id: str) -> list[OutboundMessage]:
        with self._lock:
            if self.cache and incident_id in self._messages:
                return list(self._messages[incident_id])
        found = [
            OutboundMessage.model_validate_json(item["doc"]["S"])
            for item in self._query(f"INC#{incident_id}", prefix="MSG#")
        ]
        if self.cache:
            with self._lock:
                self._messages.setdefault(incident_id, list(found))
        return found

    def claim(self, key: str) -> bool:
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={
                    "PK": _s(f"CLAIM#{key}"),
                    "SK": _s("CLAIM"),
                    "ttl": _n(int(time.time()) + CLAIM_TTL_SECONDS),
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except ClientError as exc:
            if not conditional_failed(exc):
                raise
            return False


class DynamoStore:
    """`Repository` for one incident, backed by a shared `DynamoBackend`.

    Scoped to an incident because decision ids are only unique within one (`dec-001`), exactly as
    an `InMemoryStore` holds one drill. Every view in a process shares the backend's identity map.
    """

    def __init__(
        self, backend: DynamoBackend, incident_id: str, resident_ids: list[str] | None = None
    ) -> None:
        self.backend = backend
        self.incident_id = incident_id
        self._resident_ids = set(resident_ids) if resident_ids else None

    # static
    def org(self) -> OrgProfile:
        return self.backend.org()

    def residents(self) -> list[Resident]:
        everyone = self.backend.residents()
        if self._resident_ids is None:
            return list(everyone.values())
        return [r for rid, r in everyone.items() if rid in self._resident_ids]

    def resident(self, resident_id: str) -> Resident:
        if self._resident_ids is not None and resident_id not in self._resident_ids:
            raise NotFound(f"resident {resident_id}")
        try:
            return self.backend.residents()[resident_id]
        except KeyError as e:
            raise NotFound(f"resident {resident_id}") from e

    def volunteers(self) -> list[Volunteer]:
        return list(self.backend.volunteers().values())

    def volunteer(self, volunteer_id: str) -> Volunteer:
        try:
            return self.backend.volunteers()[volunteer_id]
        except KeyError as e:
            raise NotFound(f"volunteer {volunteer_id}") from e

    def relief_centres(self) -> list[ReliefCentre]:
        return self.backend.relief_centres()

    # incidents
    def save_incident(self, incident: Incident) -> None:
        self.backend.save_incident(incident)

    def incident(self, incident_id: str) -> Incident:
        return self.backend.incident(incident_id)

    def save_case(self, case: ResidentCase) -> None:
        self.backend.save_case(case)

    def case(self, incident_id: str, resident_id: str) -> ResidentCase:
        return self.backend.case(incident_id, resident_id)

    def cases(self, incident_id: str) -> list[ResidentCase]:
        return self.backend.cases(incident_id)

    def save_decision(self, decision: Decision) -> None:
        self.backend.save_decision(decision)

    def decision(self, decision_id: str) -> Decision:
        return self.backend.decision(self.incident_id, decision_id)

    def decisions(self, incident_id: str, status: str | None = None) -> list[Decision]:
        return self.backend.decisions(incident_id, status)

    def decision_for_tool_use(self, incident_id: str, tool_use_id: str) -> Decision | None:
        if not tool_use_id:
            return None
        return next(
            (d for d in self.backend.decisions(incident_id) if d.tool_use_id == tool_use_id), None
        )

    def append_event(self, event: AuditEvent) -> None:
        self.backend.append_event(event)

    def events(self, incident_id: str, since_seq: int = 0) -> list[AuditEvent]:
        return self.backend.events(incident_id, since_seq)

    def next_seq(self, incident_id: str) -> int:
        return self.backend.allocate(incident_id, "evt")

    def allocate(self, incident_id: str, counter: str) -> int:
        return self.backend.allocate(incident_id, counter)

    def claim_decision(
        self, decision_id: str, *, responder: str, response: str, responded_at: datetime
    ) -> Decision | None:
        return self.backend.claim_decision(
            self.incident_id,
            decision_id,
            responder=responder,
            response=response,
            responded_at=responded_at,
        )

    def claim(self, key: str) -> bool:
        return self.backend.claim(key)

    def append_message(self, incident_id: str, message: OutboundMessage) -> None:
        self.backend.append_message(incident_id, message)

    def messages(self, incident_id: str) -> list[OutboundMessage]:
        return self.backend.messages(incident_id)


def create_table(client: Any, table_name: str) -> None:
    """The table as CDK defines it, for moto tests. Keep in step with `infra/doorstep_stack.py`."""
    client.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
    )
