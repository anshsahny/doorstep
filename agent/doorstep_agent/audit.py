"""Audit log and the Strands hook that feeds it (PLAN Phase 1 task 7, SPEC §8, §10).

Every tool call, every policy decision (allow or deny, with the reason and the session context
it was decided on) and every model rationale becomes an `AuditEvent`. Built on the Strands
Agents SDK hook system: `AuditHook` subscribes to `AfterToolCallEvent`.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from .models import AuditEvent, AuditType
from .store import Repository

DENY_PREFIX = "DENIED:"
_HIDDEN_KEYS = {"message"}  # long free text: summarised, never copied whole


def summarise_input(tool_input: dict[str, Any], limit: int = 160) -> str:
    parts: list[str] = []
    for key, value in tool_input.items():
        if key in _HIDDEN_KEYS and isinstance(value, str):
            parts.append(f"{key}=<{len(value)} chars>")
        else:
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            parts.append(f"{key}={text}")
    joined = ", ".join(parts)
    return joined if len(joined) <= limit else joined[: limit - 1] + "…"


class AuditLog:
    """Append-only event log for one incident."""

    def __init__(self, store: Repository, incident_id: str) -> None:
        self.store = store
        self.incident_id = incident_id

    def record(
        self,
        *,
        actor: str,
        type: AuditType,
        resident_id: str | None = None,
        tool: str | None = None,
        input_summary: str = "",
        policy_decision: Literal["allow", "deny"] | None = None,
        reason: str = "",
        rationale: str = "",
        data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            seq=self.store.next_seq(self.incident_id),
            incident_id=self.incident_id,
            actor=actor,
            type=type,
            resident_id=resident_id,
            tool=tool,
            input_summary=input_summary,
            policy_decision=policy_decision,
            reason=reason,
            rationale=rationale,
            data=data or {},
        )
        self.store.append_event(event)
        return event

    def events(self) -> list[AuditEvent]:
        return self.store.events(self.incident_id)

    def policy_denials(self) -> list[AuditEvent]:
        """Tool calls refused by Cedar."""
        return [
            e
            for e in self.events()
            if e.type == "policy" and e.policy_decision == "deny" and e.data.get("by") != "code"
        ]

    def denials(self) -> list[AuditEvent]:
        """Every refused tool call: Cedar denials plus code-level refusals inside tools."""
        return [e for e in self.events() if e.policy_decision == "deny"]


# Which session values explain a denial of each guarded tool (SPEC §8).
_DENIAL_KEYS: dict[str, tuple[str, ...]] = {
    "place_checkin_call": (
        "mode", "callee_allowlisted", "callee_consented", "attempts_last_hour", "local_hour",
        "alert_severity",
    ),
    "assign_volunteer": ("mode", "channel", "volunteer_available", "volunteer_distance_km"),
    "broadcast_to_volunteers": ("mode", "channel"),
    "notify_family": ("mode", "channel", "family_consent"),
    "record_emergency_call": ("role",),
    "start_simulated_checkin": ("mode",),
}  # fmt: skip


def explain_denial(tool: str, session: dict[str, Any], tool_input: dict[str, Any]) -> str:
    """A plain-language line saying which facts the policy decided on."""
    facts = [f"{k}={session[k]}" for k in _DENIAL_KEYS.get(tool, ()) if k in session]
    if tool == "broadcast_to_volunteers":
        facts.append(f"include_resident_details={tool_input.get('include_resident_details')}")
    if tool == "assign_volunteer":
        facts.append(f"volunteer_id={tool_input.get('volunteer_id')}")
    return "decided on " + ", ".join(facts) if facts else "no matching permit"


class AuditHook(HookProvider):
    """Records every tool call of an agent, and every Cedar denial with its reason.

    The agent must be invoked with `invocation_state={"ctx": RunContext, ...}`; the hook reads
    the audit log from there so one hook instance can serve any agent.
    """

    def __init__(self, actor: str) -> None:
        self.actor = actor
        self._attempted: set[str] = set()

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Record the attempt, because an interrupted tool never reaches AfterToolCallEvent.

        Strands skips `AfterToolCallEvent` for a tool whose `BeforeToolCallEvent` raised an
        interrupt, so without this a pause would leave no trace: the audit log would show a
        captain being asked about a tool call it never mentioned. One line per tool use, since
        the event fires again on resume.
        """
        ctx = event.invocation_state.get("ctx")
        tool_use_id = str(event.tool_use.get("toolUseId") or "")
        if ctx is None or tool_use_id in self._attempted:
            return
        self._attempted.add(tool_use_id)
        tool_input = dict(event.tool_use.get("input") or {})
        ctx.audit.record(
            actor=self.actor,
            type="tool_call",
            resident_id=tool_input.get("resident_id") or event.invocation_state.get("resident_id"),
            tool=event.tool_use["name"],
            input_summary=summarise_input(tool_input),
            reason="attempted",
            data={"status": "attempted", "tool_use_id": tool_use_id},
        )

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        state = event.invocation_state
        ctx = state.get("ctx")
        if ctx is None:
            return
        audit: AuditLog = ctx.audit
        tool_name = event.tool_use["name"]
        tool_input = dict(event.tool_use.get("input") or {})
        resident_id = tool_input.get("resident_id") or state.get("resident_id")
        session = state.get("_cedar_session") or {}
        rationale = str(tool_input.get("reason") or "")
        cancel = event.cancel_message or ""

        if cancel.startswith(DENY_PREFIX):
            why = cancel[len(DENY_PREFIX) :].strip()
            audit.record(
                actor=self.actor,
                type="policy",
                resident_id=resident_id,
                tool=tool_name,
                input_summary=summarise_input(tool_input),
                policy_decision="deny",
                reason=f"{why}; {explain_denial(tool_name, session, tool_input)}",
                rationale=rationale,
                data={"session": session},
            )
            return

        status = event.result.get("status")
        text = " ".join(
            c.get("text", "") for c in event.result.get("content", []) if isinstance(c, dict)
        )
        audit.record(
            actor=self.actor,
            type="tool_call",
            resident_id=resident_id,
            tool=tool_name,
            input_summary=summarise_input(tool_input),
            policy_decision="allow" if session else None,
            reason=(text[:200] if status == "error" else ""),
            rationale=rationale,
            data={"status": status, "cancelled": bool(cancel), "session": session},
        )
