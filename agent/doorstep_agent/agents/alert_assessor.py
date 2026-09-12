"""alert_assessor: decides whether an NWS alert activates the org's hazard profile (SPEC §6, F1).

A Strands Agent with structured output. The profile's `alert_events` are the only events that
can activate it; the deterministic gate in `graph.py` enforces that even if the model disagrees.
"""

from __future__ import annotations

from strands import Agent

from ..audit import AuditHook
from ..guards import ModelCallGuard
from ..models import Alert, AlertAssessment
from ..runtime import RunContext
from ..tools import get_org_profile
from ._common import bullet_list, make_model

AGENT_NAME = "alert_assessor"


def system_prompt(ctx: RunContext) -> str:
    profile, org = ctx.profile, ctx.org
    events = bullet_list(
        [
            f"{a.event} (VTEC {a.vtec or 'n/a'}): "
            + ("activates automatically" if a.activation == "auto" else "asks the captain first")
            for a in profile.alert_events
        ]
    )
    nws = org.nws
    return (
        f"You are the alert assessor for {org.name}, a neighbour check-in team. "
        "Built with Strands Agents.\n\n"
        f"The active hazard profile is '{profile.display_name}' (id: {profile.id}). "
        "It activates on these NWS events and no others:\n"
        f"{events}\n\n"
        f"The org is in {nws.get('zone_name', '')} (NWS zone {nws.get('zone', '')}, "
        f"{nws.get('county', '')} County, {nws.get('state', '')}), served by NWS office "
        f"{nws.get('office', '')}. Call get_org_profile if you need more detail.\n\n"
        "Decide:\n"
        "- activate: true only if the alert's event is on the list above AND its area covers "
        "the org (zone code in the UGC list, or the zone/county/city named in the area).\n"
        "- ask_captain: true if the matching event is marked 'asks the captain first'.\n"
        "- severity: the alert's severity (Minor, Moderate, Severe, Extreme or Unknown).\n"
        f"- hazards: ['{profile.id}'] when it matches, otherwise an empty list.\n"
        "- window: when the hazard is in effect, in plain words.\n"
        "- rationale: one or two sentences a volunteer captain can read at a glance."
    )


def build_alert_assessor(ctx: RunContext) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=make_model(ctx, temperature=0.1, max_tokens=600),
        system_prompt=system_prompt(ctx),
        tools=[get_org_profile],
        structured_output_model=AlertAssessment,
        hooks=[AuditHook(f"agent:{AGENT_NAME}"), ModelCallGuard()],
        callback_handler=None,
    )


def alert_task_text(alert: Alert) -> str:
    """The Graph task: the alert in a compact, model-readable form."""
    lines = [
        "Assess this NWS alert.",
        f"event: {alert.event}",
        f"severity: {alert.severity} | urgency: {alert.urgency} | certainty: {alert.certainty}",
        f"headline: {alert.headline}",
        f"sender: {alert.sender}",
        f"onset: {alert.onset} | expires: {alert.expires}",
        f"VTEC: {alert.vtec or 'n/a'}",
        f"UGC zones: {', '.join(alert.ugc) or 'n/a'}",
        f"area: {alert.area_desc}",
        "",
        "description:",
        alert.description[:1800],
    ]
    return "\n".join(lines)
