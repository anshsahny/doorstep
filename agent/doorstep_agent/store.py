"""Repository interface with an in-memory backend (PLAN Phase 1) and the contract DynamoDB meets.

Static org data (org profile, roster, volunteers, relief centres) is loaded from `data/`.
Incident data (incidents, cases, decisions, audit events) lives in memory for local drills;
`store_dynamo.DynamoStore` keeps the same data in DynamoDB (Phase 3).

Two properties every backend must have, because Doorstep's code relies on them:

* **Shared records within a process.** `case()` returns the record callers mutate and then
  `save_case()`; a caller holding a record while an agent changes the same case must see that
  change. InMemoryStore gets this for free; DynamoStore keeps an identity map.
* **Races are settled by the store, not by luck.** Ids come from `allocate`/`next_seq`, a
  decision leaves `pending` only through `claim_decision`, and one-time side effects go through
  `claim`. Strands runs tool bodies in threads, so "read, check, write" in the caller is a race.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol

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


class Repository(Protocol):
    """What the agents, tools and runner need from storage."""

    # static
    def org(self) -> OrgProfile: ...
    def residents(self) -> list[Resident]: ...
    def resident(self, resident_id: str) -> Resident: ...
    def volunteers(self) -> list[Volunteer]: ...
    def volunteer(self, volunteer_id: str) -> Volunteer: ...
    def relief_centres(self) -> list[ReliefCentre]: ...

    # incidents
    def save_incident(self, incident: Incident) -> None: ...
    def incident(self, incident_id: str) -> Incident: ...
    def save_case(self, case: ResidentCase) -> None: ...
    def case(self, incident_id: str, resident_id: str) -> ResidentCase: ...
    def cases(self, incident_id: str) -> list[ResidentCase]: ...
    def save_decision(self, decision: Decision) -> None: ...
    def decision(self, decision_id: str) -> Decision: ...
    def decisions(self, incident_id: str, status: str | None = None) -> list[Decision]: ...
    def decision_for_tool_use(self, incident_id: str, tool_use_id: str) -> Decision | None: ...
    def append_event(self, event: AuditEvent) -> None: ...
    def events(self, incident_id: str, since_seq: int = 0) -> list[AuditEvent]: ...
    def next_seq(self, incident_id: str) -> int: ...

    # concurrency-safe primitives
    def allocate(self, incident_id: str, counter: str) -> int: ...
    def claim_decision(
        self, decision_id: str, *, responder: str, response: str, responded_at: datetime
    ) -> Decision | None: ...
    def claim(self, key: str) -> bool: ...
    def append_message(self, incident_id: str, message: OutboundMessage) -> None: ...
    def messages(self, incident_id: str) -> list[OutboundMessage]: ...


class NotFound(KeyError):
    """Raised when a record does not exist."""


class InMemoryStore:
    """Dict-backed repository for local drills and tests."""

    def __init__(
        self,
        org: OrgProfile,
        residents: list[Resident],
        volunteers: list[Volunteer],
        relief_centres: list[ReliefCentre],
    ) -> None:
        self._org = org
        self._residents = {r.id: r for r in residents}
        self._volunteers = {v.id: v for v in volunteers}
        self._relief_centres = list(relief_centres)
        self._incidents: dict[str, Incident] = {}
        self._cases: dict[tuple[str, str], ResidentCase] = {}
        self._decisions: dict[str, Decision] = {}
        self._events: dict[str, list[AuditEvent]] = {}
        self._messages: dict[str, list[OutboundMessage]] = {}
        self._counters: dict[tuple[str, str], int] = {}
        self._claims: set[str] = set()
        self._lock = threading.Lock()

    @classmethod
    def from_data_dir(
        cls, data_dir: Path, *, resident_ids: list[str] | None = None
    ) -> InMemoryStore:
        """Load the static JSON files. `resident_ids` restricts the roster (drill subsets)."""
        org = OrgProfile.model_validate(_read(data_dir / "org.json"))
        roster = _read(data_dir / "roster.json")
        residents = [Resident.model_validate(r) for r in roster["residents"]]
        if resident_ids is not None:
            wanted = set(resident_ids)
            residents = [r for r in residents if r.id in wanted]
            missing = wanted - {r.id for r in residents}
            if missing:
                raise NotFound(f"residents not in roster: {sorted(missing)}")
        volunteers = [
            Volunteer.model_validate(v) for v in _read(data_dir / "volunteers.json")["volunteers"]
        ]
        centres = [
            ReliefCentre.model_validate(c)
            for c in _read(data_dir / "relief_centres.json")["centres"]
        ]
        return cls(org, residents, volunteers, centres)

    # --- static ---

    def org(self) -> OrgProfile:
        return self._org

    def residents(self) -> list[Resident]:
        return list(self._residents.values())

    def resident(self, resident_id: str) -> Resident:
        try:
            return self._residents[resident_id]
        except KeyError as e:
            raise NotFound(f"resident {resident_id}") from e

    def volunteers(self) -> list[Volunteer]:
        return list(self._volunteers.values())

    def volunteer(self, volunteer_id: str) -> Volunteer:
        try:
            return self._volunteers[volunteer_id]
        except KeyError as e:
            raise NotFound(f"volunteer {volunteer_id}") from e

    def relief_centres(self) -> list[ReliefCentre]:
        return list(self._relief_centres)

    # --- incidents ---

    def save_incident(self, incident: Incident) -> None:
        self._incidents[incident.id] = incident

    def incident(self, incident_id: str) -> Incident:
        try:
            return self._incidents[incident_id]
        except KeyError as e:
            raise NotFound(f"incident {incident_id}") from e

    def save_case(self, case: ResidentCase) -> None:
        self._cases[(case.incident_id, case.resident_id)] = case

    def case(self, incident_id: str, resident_id: str) -> ResidentCase:
        try:
            return self._cases[(incident_id, resident_id)]
        except KeyError as e:
            raise NotFound(f"case {incident_id}/{resident_id}") from e

    def cases(self, incident_id: str) -> list[ResidentCase]:
        return [c for (inc, _), c in list(self._cases.items()) if inc == incident_id]

    def save_decision(self, decision: Decision) -> None:
        self._decisions[decision.id] = decision

    def decision(self, decision_id: str) -> Decision:
        try:
            return self._decisions[decision_id]
        except KeyError as e:
            raise NotFound(f"decision {decision_id}") from e

    def decisions(self, incident_id: str, status: str | None = None) -> list[Decision]:
        return [
            d
            for d in list(self._decisions.values())
            if d.incident_id == incident_id and (status is None or d.status == status)
        ]

    def decision_for_tool_use(self, incident_id: str, tool_use_id: str) -> Decision | None:
        """The decision raised by one tool use, if any.

        A tool that raises an interrupt re-runs from the top on resume, so it must find the
        record it already created instead of creating a second one.
        """
        if not tool_use_id:
            return None
        return next(
            (
                d
                for d in list(self._decisions.values())
                if d.incident_id == incident_id and d.tool_use_id == tool_use_id
            ),
            None,
        )

    def append_event(self, event: AuditEvent) -> None:
        self._events.setdefault(event.incident_id, []).append(event)

    def events(self, incident_id: str, since_seq: int = 0) -> list[AuditEvent]:
        return [e for e in self._events.get(incident_id, []) if e.seq > since_seq]

    def next_seq(self, incident_id: str) -> int:
        """Allocate the next audit sequence number. Never hands the same number out twice."""
        return self._allocate(incident_id, "evt", len(self._events.get(incident_id, [])))

    def allocate(self, incident_id: str, counter: str) -> int:
        # `list(...)` snapshots the values in one step: tool bodies run in threads and may save a
        # decision while this counts (found with tight thread switching, 2026-09-13).
        existing = (
            len([d for d in list(self._decisions.values()) if d.incident_id == incident_id])
            if counter == "dec"
            else 0
        )
        return self._allocate(incident_id, counter, existing)

    def _allocate(self, incident_id: str, counter: str, floor: int) -> int:
        with self._lock:
            n = max(self._counters.get((incident_id, counter), 0), floor) + 1
            self._counters[(incident_id, counter)] = n
            return n

    def claim_decision(
        self, decision_id: str, *, responder: str, response: str, responded_at: datetime
    ) -> Decision | None:
        """Move a decision from pending to answered, once. None if someone got there first."""
        with self._lock:
            decision = self.decision(decision_id)
            if decision.status != "pending":
                return None
            decision.status = "answered"
            decision.responder = responder
            decision.response = response
            decision.responded_at = responded_at
            return decision

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._claims:
                return False
            self._claims.add(key)
            return True

    def append_message(self, incident_id: str, message: OutboundMessage) -> None:
        self._messages.setdefault(incident_id, []).append(message)

    def messages(self, incident_id: str) -> list[OutboundMessage]:
        return list(self._messages.get(incident_id, []))


def load_drill_subset(data_dir: Path) -> list[str]:
    """The resident ids the roster marks for drills."""
    return list(_read(data_dir / "roster.json")["drill_subset"])


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
