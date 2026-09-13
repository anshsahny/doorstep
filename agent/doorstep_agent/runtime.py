"""The per-incident runtime context shared by agents, tools, hooks and policies.

Agents are invoked with `invocation_state={"ctx": RunContext, ...}`; Strands forwards that state
to tools (`@tool(context=True)`), hooks and the Cedar context enricher.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from .audit import AuditLog
from .config import Settings, settings
from .models import Mode, OrgProfile, OutboundMessage
from .profiles import HazardProfile
from .state_machine import CasePolicy, Clock
from .store import Repository

__all__ = ["OutboundMessage", "RunContext", "StoreOutbox", "normalize_number", "resolve_ref"]

_REF = re.compile(r"^env:([A-Z0-9_]+)(?:\[(\d+)\])?$")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalize_number(raw: str | None) -> str | None:
    """A phone number as E.164, or None. Spaces, dashes, dots and brackets are ignored.

    Allowlist checks compare normalized numbers only, so "+1 (503) 555-0100" and "+15035550100"
    are the same number and a formatting trick cannot slip a number past the list. Keep in step
    with `doorstep_api.common.normalize_number` (a test compares the two).
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s().-]", "", raw)
    return cleaned if _E164.match(cleaned) else None


class StoreOutbox(list):
    """`ctx.outbox` that also writes each message to the store.

    A process-local list is enough for one drill in one process. Once a later process may carry
    on the same incident (a captain's tap handled after a restart), what was sent has to outlive
    the process that sent it, or the violation check and the report would see half the story.
    Callers keep using `ctx.outbox.append(...)` and iterate it as before.
    """

    def __init__(self, store: Repository, incident_id: str) -> None:
        super().__init__(store.messages(incident_id))
        self._store = store
        self._incident_id = incident_id

    def append(self, message: OutboundMessage) -> None:  # type: ignore[override]
        self._store.append_message(self._incident_id, message)
        super().append(message)


@dataclass
class RunContext:
    store: Repository
    incident_id: str
    profile: HazardProfile
    org: OrgProfile
    mode: Mode
    clock: Clock
    policy: CasePolicy
    audit: AuditLog
    settings: Settings = field(default_factory=settings)
    alert_severity: str = "Unknown"
    auto_approve: bool = False
    outbox: list[OutboundMessage] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    channel_override: str | None = None
    # A pre-built Strands model provider that replaces the configured Bedrock model. Tests and
    # evals set it to drive the real agents — real tools, hooks, policies and sessions — without
    # a network call; production leaves it None.
    model_override: Any = None
    # Where a permitted real call is queued for the dialer (`checkin_worker`). None everywhere
    # except the cloud coordinator, so no drill, test or eval can ever reach Twilio.
    call_queue: Any = None

    @property
    def channel(self) -> str:
        """Which kind of channel this incident uses: only live mode touches real ones."""
        if self.channel_override:
            return self.channel_override
        return "real" if self.mode == "live" else "simulated"

    @property
    def call_allowlist(self) -> list[str]:
        raw = os.getenv("CALL_ALLOWLIST", "")
        return [n for n in (normalize_number(x) for x in raw.split(",")) if n]

    @property
    def operator_test_number(self) -> str | None:
        """The operator's own phone, exempt from quiet hours on operator test incidents only."""
        return normalize_number(os.getenv("OPERATOR_TEST_NUMBER"))

    def local_hour(self) -> int:
        return self.clock.now().astimezone(ZoneInfo(self.org.timezone)).hour

    def invocation_state(self, **extra: Any) -> dict[str, Any]:
        """The dict passed to every Strands agent invocation."""
        return {"ctx": self, "role": "agent", **extra}


def resolve_ref(ref: str | None) -> str | None:
    """Resolve an `env:NAME` or `env:NAME[i]` reference to its value, or None.

    Values are secrets or personal data (phone numbers, chat IDs): resolve them at the last
    moment and never log them.
    """
    if not ref:
        return None
    m = _REF.match(ref)
    if not m:
        return None
    raw = os.getenv(m.group(1), "")
    if not raw:
        return None
    if m.group(2) is None:
        return raw.strip() or None
    parts = [p.strip() for p in raw.split(",")]
    idx = int(m.group(2))
    return parts[idx] if idx < len(parts) and parts[idx] else None
