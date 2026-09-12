"""Captain approval for high-risk door-knocks, as a Strands `BeforeToolCallEvent` hook.

Why a hook and not a tool interrupt (PLAN Phase 2 task 1 makes this split; Spike A proved it):

* `tool_context.interrupt` pauses *inside* the tool, and on resume the tool body re-runs from the
  top. Anything it did before interrupting happens twice. That suits `escalate_to_captain`, whose
  pre-interrupt work is one idempotent upsert, and where paging the captain *is* the point.
* `BeforeToolCallEvent.interrupt` pauses *at admission*: the executor returns before the tool
  runs at all, and on resume the tool body runs exactly once — or never, if the hook cancels it.
  That is the only correct shape for `assign_volunteer`, because its side effect is a message
  containing a resident's details leaving the building. It must not be sent and then regretted.

The hook only interrupts when the tool would otherwise go ahead: never for a low-risk assignment,
never for one Cedar is about to refuse, and never for one the captain has *already* approved.
"""

from __future__ import annotations

from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from .decisions import upsert_decision
from .geo import haversine_km
from .models import CaseState, DecisionOption
from .runtime import RunContext
from .state_machine import transition
from .store import NotFound

APPROVE_DOOR_KNOCK = "doorstep-approve-door-knock"
HIGH_RISK_WAVE = 1


class ApprovalHook(HookProvider):
    """Pauses `assign_volunteer` for the captain when the resident is high risk."""

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != "assign_volunteer":
            return
        state = event.invocation_state
        ctx: RunContext | None = state.get("ctx")
        if ctx is None:
            return

        tool_input = dict(event.tool_use.get("input") or {})
        resident_id = str(tool_input.get("resident_id") or "")
        volunteer_id = str(tool_input.get("volunteer_id") or "")
        try:
            volunteer = ctx.store.volunteer(volunteer_id)
            case = ctx.store.case(ctx.incident_id, resident_id)
        except NotFound:
            return  # the tool itself reports the unknown id; nothing to approve

        if case.risk.wave != HIGH_RISK_WAVE:
            return
        if _already_approved(ctx, state, resident_id, volunteer_id):
            return
        if not volunteer.available or volunteer.role != "volunteer":
            return  # Cedar and the tool's own check refuse this; don't wake the captain for it

        captain = ctx.org.captain_id
        first = volunteer.name.split()[0]
        # Measured here, not read from `_cedar_session`: that snapshot belongs to whichever tool
        # call wrote it last, so a preceding `find_nearest_volunteers` (no volunteer_id, hence
        # the enricher's 9999 sentinel) put "Send Sam (9999 km)" on the captain's phone.
        resident = ctx.store.resident(resident_id)
        distance = haversine_km(resident.lat, resident.lng, volunteer.lat, volunteer.lng)
        where = f"{first} ({distance:.1f} km)"
        decision = upsert_decision(
            ctx,
            tool_use_id=str(event.tool_use.get("toolUseId") or ""),
            resident_id=resident_id,
            name=APPROVE_DOOR_KNOCK,
            reason=str(tool_input.get("reason") or "high-risk visit"),
            options=[
                DecisionOption(
                    id="approve",
                    label=f"Send {where}",
                    action="assign_volunteer",
                    args={"resident_id": resident_id, "volunteer_id": volunteer_id},
                ),
                DecisionOption(
                    id="handle",
                    label="I'm handling it",
                    action="resolve",
                    args={"resident_id": resident_id, "outcome": "captain handling"},
                ),
            ],
            audience=captain,
        )
        # Park the case while the captain decides, so the board and the drill loop can see it.
        if case.state == CaseState.NEEDS_HELP:
            transition(case, CaseState.ESCALATED, reason=f"door-knock by {volunteer.id} pending")
            ctx.store.save_case(case)

        answer = event.interrupt(APPROVE_DOOR_KNOCK, reason={"decision_id": decision.id})

        if not isinstance(answer, dict) or answer.get("option_id") != "approve":
            label = answer.get("label", "declined") if isinstance(answer, dict) else str(answer)
            event.cancel_tool = f"the captain chose: {label}"
            return
        # Approved: let the tool run, and tell it which decision authorised it so the resumed
        # call is recorded as the captain's, not the agent's.
        state["approved_decision_id"] = decision.id
        state["approved_by"] = answer.get("responder", "")


def _already_approved(
    ctx: RunContext, state: dict[str, Any], resident_id: str, volunteer_id: str
) -> bool:
    """True when this assignment is carrying out a decision the captain already answered.

    SPEC §4.1 offers the captain "Send Tom" as an option, and SPEC §7 says every high-risk
    assignment interrupts. Taken literally the captain is asked twice for one door-knock. The
    answered decision they are executing is the authority, so the second ask is skipped.
    """
    decision_id = state.get("answered_decision_id")
    if not decision_id:
        return False
    try:
        decision = ctx.store.decision(str(decision_id))
    except NotFound:
        return False
    if decision.status != "answered" or decision.resident_id != resident_id:
        return False
    chosen = decision.option(decision.response or "")
    return bool(
        chosen
        and chosen.action == "assign_volunteer"
        and chosen.args.get("volunteer_id") == volunteer_id
    )
