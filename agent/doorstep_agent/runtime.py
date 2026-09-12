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
from .models import Mode, OrgProfile
from .profiles import HazardProfile
from .state_machine import CasePolicy, Clock
from .store import Repository

_REF = re.compile(r"^env:([A-Z0-9_]+)(?:\[(\d+)\])?$")


@dataclass
class OutboundMessage:
    """Something Doorstep would say or send. In a drill it is recorded, never delivered."""

    kind: str  # resident_tip | volunteer_task | captain_alert | family_notice | group_broadcast
    recipient: str  # resident id, volunteer id, "captain", "family:<ref>", "volunteers"
    text: str
    resident_id: str | None = None


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

    @property
    def channel(self) -> str:
        """Which kind of channel this incident uses: only live mode touches real ones."""
        if self.channel_override:
            return self.channel_override
        return "real" if self.mode == "live" else "simulated"

    @property
    def call_allowlist(self) -> list[str]:
        raw = os.getenv("CALL_ALLOWLIST", "")
        return [n.strip() for n in raw.split(",") if n.strip()]

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
