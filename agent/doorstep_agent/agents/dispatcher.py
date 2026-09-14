"""dispatcher: acts on a check-in result with policy-guarded tools (SPEC §7, §8, F5).

A Strands Agent whose every tool call passes through `CedarAuthorization` and is written to the
audit log by `AuditHook`. Deterministic code (the tools) enforces state transitions and minimal
disclosure; the model chooses which help fits and writes the reasons.
"""

from __future__ import annotations

import asyncio
from typing import Any

from strands import Agent
from strands.agent import AgentResult

from ..approvals import ApprovalHook
from ..audit import AuditHook
from ..decisions import stamp_pending
from ..guards import ModelCallGuard
from ..models import CheckinResult, Decision, Resident, ResidentCase
from ..policies import build_cedar
from ..runtime import RunContext
from ..sessions import build_session_manager, session_id
from ..tools import DISPATCH_TOOLS
from ._common import make_model

AGENT_NAME = "dispatcher"


def system_prompt(ctx: RunContext) -> str:
    profile = ctx.profile
    return (
        f"You are the dispatcher for {ctx.org.name} during a '{profile.display_name}' incident. "
        "Built with Strands Agents. You act on one resident's check-in result using tools. Every "
        "tool takes a `reason`: write one plain sentence a captain can read later.\n\n"
        "Rules, in priority order:\n"
        "1. URGENT: call escalate_to_captain FIRST, with the key quote and what the resident was "
        "told (they were told to call 911). Doorstep never calls emergency services and never "
        "records an emergency call itself. Do nothing else for an urgent case; the captain "
        "decides. Then stop.\n"
        "2. NEEDS_HELP: if they need a cooler or safer place to go, or a ride, send_resident_tip "
        f"with kind 'relief_centres' (relief-centre kind: '{profile.relief_centre_kind}'). "
        "Send the safety tip (kind 'tip') to everyone who needs help. For water, food, "
        "medication access, company or a ride: call find_nearest_volunteers and assign_volunteer "
        "to the nearest AVAILABLE volunteer within 3 km, with include_brief true. If nobody is "
        "available, call escalate_to_captain (unmet need). notify_family only when it clearly "
        "helps and the resident consented. If you assigned a volunteer, do NOT call close_case: "
        "the volunteer's reply closes the case. Otherwise finish with close_case (outcome "
        "'helped'), unless the case is now awaiting the captain.\n"
        "3. OK: send_resident_tip kind 'tip', schedule_recheck for "
        f"{profile.recheck_minutes} minutes, then close_case with outcome 'ok'.\n"
        "4. No answer after three attempts (the case is ESCALATED): call escalate_to_captain so "
        "the captain can approve a door-knock; suggest the nearest available volunteer.\n"
        "5. One decision per resident: once the captain has been asked about a resident, or has "
        "chosen 'I'm handling it', never escalate that resident again; do what the captain "
        "chose, then stop.\n\n"
        "Never: broadcast resident details to the group, record an emergency call, place a real "
        "phone call in a drill, or share another resident's information. If a tool is denied, do "
        "not retry it; choose a permitted action or escalate. Keep briefs to what the volunteer "
        "needs: first name (if consented), address label, what to expect. No health detail beyond "
        "what the tools already include. Never guess a resident's pronouns from their name: use "
        "their first name, or 'they'. When done, reply with one line summarising what you did."
    )


def build_dispatcher(ctx: RunContext, resident_id: str, *, model: Any = None) -> Agent:
    """The dispatcher for one resident's case, attached to that case's persisted session.

    The session is what makes a paused case survive this process. Rebuilding with the same
    `session_id` restores the conversation, the pending tool execution and the unanswered
    interrupt, so a captain can answer minutes later from anywhere (Spike B).
    """
    session = session_id(ctx.incident_id, resident_id, AGENT_NAME)
    return Agent(
        name=AGENT_NAME,
        model=model or ctx.model_override or make_model(ctx, temperature=0.1, max_tokens=900),
        system_prompt=system_prompt(ctx),
        tools=DISPATCH_TOOLS,
        interventions=[build_cedar(ctx)],
        hooks=[
            AuditHook(f"agent:{AGENT_NAME}"),
            ApprovalHook(),
            ModelCallGuard(max_calls=14),
        ],
        session_manager=build_session_manager(ctx.settings, session),
        callback_handler=None,
    )


def case_brief(
    ctx: RunContext, case: ResidentCase, resident: Resident, result: CheckinResult
) -> str:
    consent = resident.consent
    bs = result.backstop
    lines = [
        f"Resident: {resident.id} ({resident.first_name}), age {resident.age_band}, "
        f"{'lives alone' if resident.lives_alone else 'does not live alone'}, "
        f"language {resident.language}, at {resident.address_label}.",
        f"Risk: {case.risk.points} points (wave {case.risk.wave}; factors {case.risk.factors}).",
        "Consent: family contact "
        f"{'yes' if consent.family and resident.family_contact_ref else 'no'}; "
        f"share details with a volunteer {'yes' if consent.share_with_volunteer else 'no'}.",
        f"Case state: {case.state}; attempts so far: {case.attempts}.",
        f"Check-in result: {result.status}; needs {result.needs or 'none'}; "
        f"red flags {result.red_flags or 'none'}; confidence {result.confidence:.2f}.",
        f"Summary: {result.summary}",
        f'Key quote: "{result.key_quote}"' if result.key_quote else "Key quote: none",
    ]
    if result.flagged_mid_call:
        lines.append("The check-in agent flagged this call urgent mid-call.")
    if bs and bs.raised:
        lines.append(
            f"The deterministic backstop raised the status from {bs.model_status} to URGENT "
            f"because the resident said: {bs.matched_phrases}."
        )
    lines.append("Act now using the tools, then reply with one line.")
    return "\n".join(lines)


def _record_pauses(ctx: RunContext, resident_id: str, outcome: AgentResult) -> list[Decision]:
    """Turn every raised interrupt into an answerable decision.

    Until this runs the decision is a `draft` with no interrupt id, and `respond_to_decision`
    refuses it. Nothing is delivered to a human before it, so a button can never exist for a
    decision that cannot yet be answered.
    """
    session = session_id(ctx.incident_id, resident_id, AGENT_NAME)
    paused: list[Decision] = []
    for interrupt in outcome.interrupts or []:
        decision_id = (interrupt.reason or {}).get("decision_id")
        if not decision_id:
            continue
        decision = stamp_pending(
            ctx, ctx.store.decision(decision_id), interrupt_id=interrupt.id, session=session
        )
        ctx.audit.record(
            actor=f"agent:{AGENT_NAME}",
            type="decision",
            resident_id=resident_id,
            reason=f"{decision.id} [{decision.name}] is waiting for {decision.audience}",
            data={"decision_id": decision.id, "interrupt_id": interrupt.id, "session": session},
        )
        paused.append(decision)
    return paused


async def _run(
    ctx: RunContext,
    resident_id: str,
    prompt: Any,
    *,
    actor: str,
    role: str = "agent",
    model: Any = None,
    extra_state: dict[str, Any] | None = None,
) -> str:
    """One dispatcher invocation, which may end in a human decision instead of an answer."""
    agent = build_dispatcher(ctx, resident_id, model=model)
    state = ctx.invocation_state(resident_id=resident_id, actor=actor)
    state["role"] = role
    state.update(extra_state or {})
    try:
        outcome: AgentResult = await asyncio.wait_for(
            agent.invoke_async(prompt, invocation_state=state),
            ctx.settings.step_timeout_seconds * 2,
        )
    except Exception as exc:  # noqa: BLE001 - one failed case must not stop the incident
        ctx.audit.record(
            actor=actor,
            type="note",
            resident_id=resident_id,
            reason=f"dispatcher failed: {type(exc).__name__}: {exc}",
        )
        return f"dispatcher failed: {exc}"

    if outcome.stop_reason == "interrupt":
        paused = _record_pauses(ctx, resident_id, outcome)
        return "waiting for " + ", ".join(f"{d.id} ({d.name})" for d in paused)

    text = str(outcome).strip()
    ctx.audit.record(actor=actor, type="note", resident_id=resident_id, rationale=text[:400])
    return text


async def dispatch(
    ctx: RunContext,
    case: ResidentCase,
    resident: Resident,
    result: CheckinResult,
    *,
    model: Any = None,
) -> str:
    return await _run(
        ctx,
        resident.id,
        case_brief(ctx, case, resident, result),
        actor=f"agent:{AGENT_NAME}",
        model=model,
    )


async def resume_dispatch(
    ctx: RunContext,
    decision: Decision,
    payload: dict[str, Any],
    *,
    role: str,
    actor: str,
    model: Any = None,
) -> str:
    """Continue a paused case with a human's answer (the resume half of PLAN Phase 2 task 3).

    The invocation carries the responder's role, so a tool the agent may not call — recording an
    emergency call, say — is legal here precisely because a captain is the one acting.
    """
    if not (decision.resident_id and decision.interrupt_id):
        return "nothing to resume"
    return await _run(
        ctx,
        decision.resident_id,
        [{"interruptResponse": {"interruptId": decision.interrupt_id, "response": payload}}],
        actor=actor,
        role=role,
        model=model,
        extra_state={"answered_decision_id": decision.id},
    )
