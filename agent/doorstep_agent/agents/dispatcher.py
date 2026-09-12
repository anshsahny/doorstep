"""dispatcher: acts on a check-in result with policy-guarded tools (SPEC §7, §8, F5).

A Strands Agent whose every tool call passes through `CedarAuthorization` and is written to the
audit log by `AuditHook`. Deterministic code (the tools) enforces state transitions and minimal
disclosure; the model chooses which help fits and writes the reasons.
"""

from __future__ import annotations

import asyncio

from strands import Agent

from ..audit import AuditHook
from ..guards import ModelCallGuard
from ..models import CheckinResult, Resident, ResidentCase
from ..policies import build_cedar
from ..runtime import RunContext
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
        "helps and the resident consented. Finish with close_case (outcome 'helped'), unless the "
        "case is now awaiting the captain.\n"
        "3. OK: send_resident_tip kind 'tip', schedule_recheck for "
        f"{profile.recheck_minutes} minutes, then close_case with outcome 'ok'.\n"
        "4. No answer after three attempts (the case is ESCALATED): call escalate_to_captain so "
        "the captain can approve a door-knock; suggest the nearest available volunteer.\n\n"
        "Never: broadcast resident details to the group, record an emergency call, place a real "
        "phone call in a drill, or share another resident's information. If a tool is denied, do "
        "not retry it; choose a permitted action or escalate. Keep briefs to what the volunteer "
        "needs: first name (if consented), address label, what to expect. No health detail beyond "
        "what the tools already include. When done, reply with one line summarising what you did."
    )


def build_dispatcher(ctx: RunContext) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=make_model(ctx, temperature=0.1, max_tokens=900),
        system_prompt=system_prompt(ctx),
        tools=DISPATCH_TOOLS,
        interventions=[build_cedar(ctx)],
        hooks=[AuditHook(f"agent:{AGENT_NAME}"), ModelCallGuard(max_calls=14)],
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


async def dispatch(
    ctx: RunContext, case: ResidentCase, resident: Resident, result: CheckinResult
) -> str:
    actor = f"agent:{AGENT_NAME}"
    agent = build_dispatcher(ctx)
    try:
        outcome = await asyncio.wait_for(
            agent.invoke_async(
                case_brief(ctx, case, resident, result),
                invocation_state=ctx.invocation_state(resident_id=resident.id, actor=actor),
            ),
            ctx.settings.step_timeout_seconds * 2,
        )
    except Exception as exc:  # noqa: BLE001 - one failed case must not stop the incident
        ctx.audit.record(
            actor=actor,
            type="note",
            resident_id=resident.id,
            reason=f"dispatcher failed: {type(exc).__name__}: {exc}",
        )
        return f"dispatcher failed: {exc}"
    text = str(outcome).strip()
    ctx.audit.record(actor=actor, type="note", resident_id=resident.id, rationale=text[:400])
    return text
