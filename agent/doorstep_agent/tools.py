"""Doorstep tools (SPEC §7), written with the Strands Agents SDK `@tool` decorator.

Every tool reads the incident `RunContext` from `tool_context.invocation_state["ctx"]`. Tools
with side effects are guarded twice: by the Cedar policies (`policies.py`) at the tool boundary,
and by a code check inside the tool. In drills nothing leaves the process: messages that would
go to residents, volunteers or the captain are appended to `ctx.outbox`.

Nothing hazard-specific is hard-coded: tips, relief-centre kinds and vocabularies come from the
active hazard profile.
"""

from __future__ import annotations

import re
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from .geo import haversine_km
from .models import (
    CaseState,
    Decision,
    DecisionOption,
    ReliefCentre,
    Resident,
    ResidentCase,
    Volunteer,
)
from .risk import score_all
from .runtime import OutboundMessage, RunContext, resolve_ref
from .state_machine import ALLOWED, IllegalTransition, transition
from .store import NotFound

MAX_VOLUNTEER_DISTANCE_KM = 3.0


def _ctx(tool_context: ToolContext) -> RunContext:
    return tool_context.invocation_state["ctx"]


def _actor(tool_context: ToolContext) -> str:
    return str(tool_context.invocation_state.get("actor", "agent:unknown"))


def _case(ctx: RunContext, resident_id: str) -> ResidentCase:
    return ctx.store.case(ctx.incident_id, resident_id)


def _refuse(
    ctx: RunContext, tool_context: ToolContext, tool_name: str, reason: str, **data: Any
) -> str:
    """A code-level refusal inside a tool: audited as a deny by code, returned to the model."""
    ctx.audit.record(
        actor="system:code-check",
        type="policy",
        resident_id=data.pop("resident_id", None),
        tool=tool_name,
        policy_decision="deny",
        reason=reason,
        data={"by": "code", **data},
    )
    return f"refused: {reason}"


def _distance_km(
    a: Resident | Volunteer | ReliefCentre, b: Resident | Volunteer | ReliefCentre
) -> float:
    return haversine_km(a.lat, a.lng, b.lat, b.lng)


# --- read-only ---


@tool(context=True)
def get_org_profile(tool_context: ToolContext) -> dict[str, Any]:
    """Return the organisation's profile and the active hazard profile's activation rules.

    Use it to judge whether an alert covers this organisation and whether it should activate.
    """
    ctx = _ctx(tool_context)
    org, profile = ctx.org, ctx.profile
    return {
        "org": {
            "id": org.id,
            "name": org.name,
            "description": org.description,
            "location": org.location.model_dump(),
            "timezone": org.timezone,
            "nws": org.nws,
        },
        "hazard_profile": {
            "id": profile.id,
            "display_name": profile.display_name,
            "alert_events": [
                {"event": a.event, "vtec": a.vtec, "activation": a.activation}
                for a in profile.alert_events
            ],
        },
    }


@tool(context=True)
def get_roster(tool_context: ToolContext) -> list[dict[str, Any]]:
    """Return the residents in this incident with the facts that matter for triage.

    No contact details are included. Notes are what the team remembers about the resident.
    """
    ctx = _ctx(tool_context)
    return [
        {
            "resident_id": r.id,
            "first_name": r.first_name,
            "building": r.building,
            "language": r.language,
            "age_band": r.age_band,
            "lives_alone": r.lives_alone,
            "has_ac": r.has_ac,
            "power_dependent": r.power_dependent,
            "mobility_limited": r.mobility_limited,
            "chronic_flag": r.chronic_flag,
            "prior_no_answer": r.prior_no_answer,
            "notes": r.notes,
        }
        for r in ctx.store.residents()
    ]


@tool(context=True)
def score_residents(tool_context: ToolContext) -> list[dict[str, Any]]:
    """Deterministic risk score, contributing factors and call wave for every resident.

    Weights come from the active hazard profile. Wave 1 is called first.
    """
    ctx = _ctx(tool_context)
    scores = score_all(ctx.store.residents(), ctx.profile)
    return [s.model_dump() for s in scores.values()]


@tool(context=True)
def get_resident_memory(resident_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """What the team remembers about a resident from earlier contact.

    Args:
        resident_id: The resident's id.
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
    except NotFound:
        return {"resident_id": resident_id, "error": "unknown resident"}
    return {
        "resident_id": r.id,
        "notes": r.notes,
        "language": r.language,
        "interpreter_preference": r.interpreter_preference,
    }


@tool(context=True)
def find_relief_centres(
    kind: str, resident_id: str, tool_context: ToolContext
) -> list[dict[str, Any]]:
    """Public relief centres of one kind, nearest first.

    Args:
        kind: The active hazard profile's relief-centre kind.
        resident_id: Measure distance from this resident; empty string measures from the org.
    """
    ctx = _ctx(tool_context)
    origin_lat, origin_lng = ctx.org.location.lat, ctx.org.location.lng
    if resident_id:
        try:
            r = ctx.store.resident(resident_id)
            origin_lat, origin_lng = r.lat, r.lng
        except NotFound:
            pass
    rows = []
    for c in ctx.store.relief_centres():
        if kind not in c.kind:
            continue
        rows.append(
            {
                "centre_id": c.id,
                "name": c.name,
                "address": c.address,
                "distance_km": round(haversine_km(origin_lat, origin_lng, c.lat, c.lng), 1),
                "hours_sample": c.hours_sample,
                "note": "sample listing - verify with 211 / county before real use",
            }
        )
    return sorted(rows, key=lambda x: x["distance_km"])


@tool(context=True)
def find_nearest_volunteers(resident_id: str, tool_context: ToolContext) -> list[dict[str, Any]]:
    """Volunteers nearest to a resident, with availability and distance in km.

    Args:
        resident_id: The resident's id.
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
    except NotFound:
        return []
    rows = [
        {
            "volunteer_id": v.id,
            "first_name": v.name.split()[0],
            "role": v.role,
            "available": v.available,
            "distance_km": round(_distance_km(r, v), 2),
            "notes": v.notes,
        }
        for v in ctx.store.volunteers()
        if v.role == "volunteer"
    ]
    return sorted(rows, key=lambda x: (not x["available"], x["distance_km"]))


# --- side effects ---


@tool(context=True)
def start_simulated_checkin(resident_id: str, reason: str, tool_context: ToolContext) -> str:
    """Queue another simulated check-in for a resident now (drills and sandbox only).

    Args:
        resident_id: The resident's id.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    if ctx.mode == "live":
        return _refuse(
            ctx,
            tool_context,
            "start_simulated_checkin",
            "no simulated check-ins in live mode",
            resident_id=resident_id,
        )
    try:
        case = _case(ctx, resident_id)
        transition(case, CaseState.QUEUED, reason=f"re-queued: {reason}")
    except NotFound:
        return f"error: no case for {resident_id}"
    except IllegalTransition as e:
        return f"error: {e}"
    case.next_action_at = ctx.clock.now()
    ctx.store.save_case(case)
    return f"{resident_id} queued for another simulated check-in"


@tool(context=True)
def place_checkin_call(resident_id: str, reason: str, tool_context: ToolContext) -> str:
    """Place a real phone check-in call to a resident. Live mode and allowlisted numbers only.

    Args:
        resident_id: The resident's id.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    if ctx.mode != "live":
        return _refuse(
            ctx,
            tool_context,
            "place_checkin_call",
            f"real calls are never placed in {ctx.mode} mode",
            resident_id=resident_id,
        )
    try:
        r = ctx.store.resident(resident_id)
    except NotFound:
        return f"error: unknown resident {resident_id}"
    number = resolve_ref(r.phone_ref)
    if not number or number not in ctx.call_allowlist:
        return _refuse(
            ctx,
            tool_context,
            "place_checkin_call",
            "callee is not on the call allowlist",
            resident_id=resident_id,
        )
    if not r.consent.calls:
        return _refuse(
            ctx,
            tool_context,
            "place_checkin_call",
            "resident has not consented to calls",
            resident_id=resident_id,
        )
    return "real calls arrive in Phase 4; nothing was dialled"


@tool(context=True)
def schedule_recheck(resident_id: str, minutes: int, reason: str, tool_context: ToolContext) -> str:
    """Schedule a re-check of a resident in a number of minutes.

    Args:
        resident_id: The resident's id.
        minutes: How many minutes from now.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    try:
        case = _case(ctx, resident_id)
    except NotFound:
        return f"error: no case for {resident_id}"
    when = ctx.policy.schedule_recheck(case, minutes)
    ctx.store.save_case(case)
    return (
        f"re-check for {resident_id} scheduled in {minutes} min ({when.strftime('%H:%M:%S')} UTC)"
    )


@tool(context=True)
def send_resident_tip(resident_id: str, kind: str, reason: str, tool_context: ToolContext) -> str:
    """Send the resident a short message in their language.

    Args:
        resident_id: The resident's id.
        kind: "tip" for the hazard safety tip, or "relief_centres" for the two nearest centres.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
    except NotFound:
        return f"error: unknown resident {resident_id}"
    lang = r.language
    if kind == "relief_centres":
        centres = [
            c
            for c in sorted(ctx.store.relief_centres(), key=lambda c: _distance_km(r, c))
            if ctx.profile.relief_centre_kind in c.kind
        ][:2]
        if not centres:
            return "no relief centres of the profile's kind are listed"
        listing = "; ".join(f"{c.name}, {c.address} ({_distance_km(r, c):.1f} km)" for c in centres)
        text = (
            f"Lugares cercanos donde puede ir: {listing}."
            if lang == "es"
            else f"Nearby places you can go: {listing}."
        )
    elif kind == "tip":
        text = ctx.profile.tip(lang)
    else:
        return "error: kind must be 'tip' or 'relief_centres'"
    ctx.outbox.append(
        OutboundMessage(kind="resident_tip", recipient=r.id, text=text, resident_id=r.id)
    )
    return f"sent to {r.id}: {text}"


def _new_decision(
    ctx: RunContext, resident_id: str | None, name: str, reason: str, options: list[DecisionOption]
) -> Decision:
    n = len(ctx.store.decisions(ctx.incident_id)) + 1
    decision = Decision(
        id=f"dec-{n:03d}",
        incident_id=ctx.incident_id,
        resident_id=resident_id,
        name=name,
        reason=reason,
        options=options,
    )
    ctx.store.save_decision(decision)
    ctx.audit.record(
        actor="system:decisions",
        type="decision",
        resident_id=resident_id,
        reason=f"{name}: {reason}",
        data={"decision_id": decision.id, "options": [o.label for o in options]},
    )
    return decision


def _volunteer_brief(ctx: RunContext, r: Resident, case: ResidentCase, why: str) -> str:
    """The minimal-disclosure task text for the assigned volunteer only."""
    who = r.first_name if r.consent.share_with_volunteer else "the resident"
    hints = " ".join(r.notes) if r.consent.share_with_volunteer and r.notes else ""
    result = case.latest_result
    said = f' They said: "{result.key_quote}".' if result and result.key_quote else ""
    return (
        f"Please check on {who} at {r.address_label}. {why}.{said} "
        f"{hints} Reply here: On my way / They're OK / Need more help."
    ).replace("  ", " ")


@tool(context=True)
def assign_volunteer(
    resident_id: str, volunteer_id: str, include_brief: bool, reason: str, tool_context: ToolContext
) -> str:
    """Send one volunteer a task to check on a resident (a door-knock or a delivery).

    The brief goes only to this volunteer. High-risk cases pause for the captain's approval.

    Args:
        resident_id: The resident's id.
        volunteer_id: The volunteer to send (see find_nearest_volunteers).
        include_brief: Include the minimal details the volunteer needs (address, what to expect).
        reason: Why this resident needs a visit, in one sentence; it is shown to the volunteer.
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
        v = ctx.store.volunteer(volunteer_id)
        case = _case(ctx, resident_id)
    except NotFound as e:
        return f"error: {e}"
    if v.role != "volunteer":
        return _refuse(
            ctx,
            tool_context,
            "assign_volunteer",
            f"{v.id} is the captain, not a volunteer",
            resident_id=resident_id,
        )
    distance = _distance_km(r, v)
    if not v.available or distance >= MAX_VOLUNTEER_DISTANCE_KM:
        return _refuse(
            ctx,
            tool_context,
            "assign_volunteer",
            f"{v.id} is unavailable or too far ({distance:.1f} km)",
            resident_id=resident_id,
        )
    if case.state not in (CaseState.NEEDS_HELP, CaseState.ESCALATED, CaseState.URGENT):
        return (
            f"error: case {resident_id} is {case.state}; only NEEDS_HELP, URGENT or "
            "ESCALATED cases get a visit"
        )

    if case.risk.wave == 1 and case.state != CaseState.ESCALATED:
        # SPEC §4.2 / §7: high-risk door-knocks need the captain's approval.
        transition(case, CaseState.ESCALATED, reason=f"door-knock by {v.id} awaiting captain")
        decision = _new_decision(
            ctx,
            r.id,
            "doorstep-approve-door-knock",
            f"Approve {v.name.split()[0]} ({distance:.1f} km) knocking on {r.address_label}? "
            f"{reason}",
            [
                DecisionOption(
                    id="approve",
                    label=f"Send {v.name.split()[0]} ({distance:.1f} km)",
                    action="assign_volunteer",
                    args={
                        "resident_id": r.id,
                        "volunteer_id": v.id,
                        "include_brief": include_brief,
                        "reason": reason,
                    },
                ),
                DecisionOption(
                    id="handle",
                    label="I'm handling it",
                    action="resolve",
                    args={"resident_id": r.id, "outcome": "captain handling"},
                ),
            ],
        )
        ctx.store.save_case(case)
        if not ctx.auto_approve:
            return (
                f"high-risk case: captain approval requested (decision {decision.id}); "
                "nothing sent yet"
            )
        decision.status, decision.responder, decision.response = (
            "answered",
            "captain:auto-approve",
            "approve",
        )
        decision.responded_at = ctx.clock.now()
        ctx.store.save_decision(decision)
        ctx.audit.record(
            actor="captain:auto-approve",
            type="decision",
            resident_id=r.id,
            reason=f"{decision.id} approved: {decision.options[0].label}",
        )

    transition(case, CaseState.ASSIGNED, reason=f"volunteer {v.id} assigned: {reason}")
    case.assigned_volunteer = v.id
    ctx.store.save_case(case)
    text = (
        _volunteer_brief(ctx, r, case, reason)
        if include_brief
        else f"Please check on the resident at {r.address_label}. {reason}."
    )
    ctx.outbox.append(
        OutboundMessage(kind="volunteer_task", recipient=v.id, text=text, resident_id=r.id)
    )
    return f"task sent to {v.name.split()[0]} ({distance:.1f} km) for {r.id}"


def _mentions_resident_details(ctx: RunContext, message: str) -> str | None:
    low = message.lower()
    for r in ctx.store.residents():
        for needle in (r.name.lower(), r.address_label.lower()):
            if needle and needle in low:
                return needle
        if r.unit and re.search(rf"\bunit\s*{re.escape(r.unit.lower())}\b", low):
            return f"unit {r.unit}"
    return None


@tool(context=True)
def broadcast_to_volunteers(
    message: str, include_resident_details: bool, reason: str, tool_context: ToolContext
) -> str:
    """Send a message to the whole volunteer group. Never include resident details.

    Args:
        message: The message. General information only (hours, meeting points, thanks).
        include_resident_details: Must be false; broadcasts with resident details are forbidden.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    leaked = _mentions_resident_details(ctx, message)
    if leaked:
        return _refuse(
            ctx,
            tool_context,
            "broadcast_to_volunteers",
            f"message names a resident ({leaked}); broadcasts must not contain resident details",
        )
    ctx.outbox.append(OutboundMessage(kind="group_broadcast", recipient="volunteers", text=message))
    return "broadcast sent to the volunteer group"


@tool(context=True)
def notify_family(resident_id: str, reason: str, tool_context: ToolContext) -> str:
    """Notify the resident's family contact. Only with the resident's consent.

    Args:
        resident_id: The resident's id.
        reason: What to tell them, in one sentence, without health details.
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
    except NotFound:
        return f"error: unknown resident {resident_id}"
    if not (r.consent.family and r.family_contact_ref):
        return _refuse(
            ctx,
            tool_context,
            "notify_family",
            "no family consent or no family contact on file",
            resident_id=resident_id,
        )
    text = (
        f"Hello, this is the {ctx.org.name} neighbour team. {r.first_name} may need a hand today "
        "because of "
        f"{ctx.profile.hazard_short('en')}. {reason} A neighbour team volunteer is following up."
    )
    ctx.outbox.append(
        OutboundMessage(
            kind="family_notice",
            recipient=f"family:{r.family_contact_ref}",
            text=text,
            resident_id=r.id,
        )
    )
    return f"family contact for {r.id} notified"


@tool(context=True)
def escalate_to_captain(
    resident_id: str, reason: str, options: list[str], tool_context: ToolContext
) -> str:
    """Interrupt the block captain with a decision only a human can make.

    Use it first for every urgent case, for a high-risk resident who did not answer three
    times, and when nobody can meet a need. The captain always gets "I'm handling it", the nearest
    available volunteer and, with consent, the family contact; add any other option you think of.

    Args:
        resident_id: The resident's id.
        reason: What the captain needs to know, in one or two plain sentences.
        options: Extra short option labels for the captain (may be empty).
    """
    ctx = _ctx(tool_context)
    try:
        r = ctx.store.resident(resident_id)
        case = _case(ctx, resident_id)
    except NotFound as e:
        return f"error: {e}"

    if case.state in (CaseState.QUEUED, CaseState.CALLING):
        return f"error: case {resident_id} has no check-in result yet"
    if case.state == CaseState.RESOLVED:
        return f"error: case {resident_id} is already resolved"
    if case.state == CaseState.URGENT:
        name = "doorstep-urgent-red-flag"
    elif case.state in (CaseState.NO_ANSWER, CaseState.UNCLEAR) or (
        case.state == CaseState.ESCALATED and not case.attempt_log[-1].answered
    ):
        name = "doorstep-high-risk-no-answer"
    else:
        name = "doorstep-unmet-need"

    canonical: list[DecisionOption] = [
        DecisionOption(
            id="handle",
            label="I'm handling it",
            action="resolve",
            args={"resident_id": r.id, "outcome": "captain handling"},
        )
    ]
    nearest = [
        (v, _distance_km(r, v))
        for v in ctx.store.volunteers()
        if v.role == "volunteer" and v.available and _distance_km(r, v) < MAX_VOLUNTEER_DISTANCE_KM
    ]
    if nearest:
        v, d = min(nearest, key=lambda x: x[1])
        canonical.append(
            DecisionOption(
                id="send_volunteer",
                label=f"Send {v.name.split()[0]} ({d:.1f} km)",
                action="assign_volunteer",
                args={
                    "resident_id": r.id,
                    "volunteer_id": v.id,
                    "include_brief": True,
                    "reason": reason,
                },
            )
        )
    if r.consent.family and r.family_contact_ref:
        canonical.append(
            DecisionOption(
                id="notify_family",
                label="Call the family contact",
                action="notify_family",
                args={"resident_id": r.id, "reason": reason},
            )
        )
    seen = {o.label.lower() for o in canonical}
    for i, extra in enumerate(options or []):
        label = extra.strip()
        if label and label.lower() not in seen and len(canonical) < 5:
            canonical.append(
                DecisionOption(
                    id=f"extra_{i}", label=label, action="note", args={"resident_id": r.id}
                )
            )
            seen.add(label.lower())

    if case.state != CaseState.ESCALATED:
        transition(case, CaseState.ESCALATED, reason=f"escalated to captain: {reason}")
        ctx.store.save_case(case)
    decision = _new_decision(ctx, r.id, name, reason, canonical)

    result = case.latest_result
    alone = "lives alone" if r.lives_alone else "does not live alone"
    quote = f' Said: "{result.key_quote}".' if result and result.key_quote else ""
    told = " Told them to call 911." if name == "doorstep-urgent-red-flag" else ""
    text = f"{r.first_name}, {r.age_band}, {alone}: {reason}{quote}{told}"
    ctx.outbox.append(
        OutboundMessage(kind="captain_alert", recipient="captain", text=text, resident_id=r.id)
    )
    labels = " / ".join(o.label for o in canonical)
    return f"captain paged (decision {decision.id}, {name}); options: {labels}"


@tool(context=True)
def record_emergency_call(resident_id: str, by: str, tool_context: ToolContext) -> str:
    """Record that a human made an emergency (911) call for a resident. Humans only.

    Args:
        resident_id: The resident's id.
        by: Who made the call (captain id, volunteer id, "resident" or "family").
    """
    ctx = _ctx(tool_context)
    role = str(tool_context.invocation_state.get("role", "agent"))
    if role != "captain":
        return _refuse(
            ctx,
            tool_context,
            "record_emergency_call",
            "only the captain may record an emergency call",
            resident_id=resident_id,
        )
    ctx.audit.record(
        actor=_actor(tool_context),
        type="note",
        resident_id=resident_id,
        reason=f"emergency call recorded, made by {by}",
    )
    return "recorded"


@tool(context=True)
def close_case(resident_id: str, outcome: str, reason: str, tool_context: ToolContext) -> str:
    """Close a resident's case.

    Args:
        resident_id: The resident's id.
        outcome: One of: ok, helped, captain_handling, no_action_needed.
        reason: Why, in one sentence.
    """
    ctx = _ctx(tool_context)
    try:
        case = _case(ctx, resident_id)
    except NotFound:
        return f"error: no case for {resident_id}"
    if CaseState.RESOLVED not in ALLOWED[case.state]:
        return f"error: cannot close a case in state {case.state}; escalate or assign first"
    transition(case, CaseState.RESOLVED, reason=f"{outcome}: {reason}")
    case.outcome = outcome
    ctx.store.save_case(case)
    return f"case {resident_id} closed: {outcome}"


# --- check-in tools (used by the check-in agent during a conversation) ---


def _call(tool_context: ToolContext) -> dict[str, Any]:
    return tool_context.invocation_state["call"]


@tool(context=True)
def record_answer(question_id: str, answer: str, tool_context: ToolContext) -> str:
    """Record what the resident answered to one protocol question.

    Args:
        question_id: The question id from the script (for example feeling).
        answer: Their answer, briefly, in their own words.
    """
    call = _call(tool_context)
    call.setdefault("answers", {})[question_id] = answer
    return f"recorded {question_id}"


@tool(context=True)
def flag_urgent(reason: str, tool_context: ToolContext) -> str:
    """Flag this call as urgent right now: a red flag or a stated emergency. Fires immediately.

    Args:
        reason: What they said or what you noticed, in one sentence.
    """
    call = _call(tool_context)
    call.setdefault("urgent", []).append(reason)
    ctx: RunContext = tool_context.invocation_state["ctx"]
    ctx.audit.record(
        actor=_actor(tool_context),
        type="checkin",
        resident_id=tool_context.invocation_state.get("resident_id"),
        reason=f"flag_urgent: {reason}",
    )
    return "flagged; say the red-flag line and end the questions"


@tool(context=True)
def end_call(summary: str, tool_context: ToolContext) -> str:
    """End the call after the closing line.

    Args:
        summary: One sentence on how they are and what they need.
    """
    call = _call(tool_context)
    call["ended"] = True
    call["summary"] = summary
    return "call ended"


READ_ONLY_TOOLS = [
    get_org_profile,
    get_roster,
    score_residents,
    get_resident_memory,
    find_relief_centres,
    find_nearest_volunteers,
]
DISPATCH_TOOLS = [
    get_resident_memory,
    find_relief_centres,
    find_nearest_volunteers,
    start_simulated_checkin,
    place_checkin_call,
    schedule_recheck,
    send_resident_tip,
    assign_volunteer,
    broadcast_to_volunteers,
    notify_family,
    escalate_to_captain,
    record_emergency_call,
    close_case,
]
CHECKIN_TOOLS = [record_answer, flag_urgent, end_call]

# Every tool any agent can call. The Cedar schema is generated from this list so that the policy
# files, which name every action, validate no matter which subset an agent carries.
ALL_TOOLS = list(
    {t.tool_name: t for t in [*READ_ONLY_TOOLS, *DISPATCH_TOOLS, *CHECKIN_TOOLS]}.values()
)
