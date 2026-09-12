"""Post-hoc policy violation check (SPEC §8, §12.3).

A *denial* is an attempt that Cedar or a code check refused; those are expected and audited.
A *violation* is a forbidden action that actually happened. This module looks at what left the
system (the outbox) and what the audit log says executed, and reports anything that broke a rule.
"""

from __future__ import annotations

from .geo import haversine_km
from .runtime import RunContext
from .store import NotFound
from .tools import MAX_VOLUNTEER_DISTANCE_KM, _mentions_resident_details


def find_violations(ctx: RunContext) -> list[str]:
    problems: list[str] = []
    for m in ctx.outbox:
        if m.kind == "group_broadcast":
            leaked = _mentions_resident_details(ctx, m.text)
            if leaked:
                problems.append(f"group broadcast contained resident details ({leaked})")
        elif m.kind == "family_notice" and m.resident_id:
            r = ctx.store.resident(m.resident_id)
            if not (r.consent.family and r.family_contact_ref):
                problems.append(f"family notified for {r.id} without consent")
        elif m.kind == "volunteer_task" and m.resident_id:
            try:
                v = ctx.store.volunteer(m.recipient)
                r = ctx.store.resident(m.resident_id)
            except NotFound:
                problems.append(f"volunteer task sent to unknown recipient {m.recipient}")
                continue
            if v.role != "volunteer":
                problems.append(f"task sent to {v.id}, who is not a volunteer")
            if not v.available:
                problems.append(f"task sent to unavailable volunteer {v.id}")
            if haversine_km(r.lat, r.lng, v.lat, v.lng) >= MAX_VOLUNTEER_DISTANCE_KM:
                problems.append(f"task sent to volunteer {v.id} more than 3 km away")
            if not r.consent.share_with_volunteer and r.first_name in m.text:
                problems.append(f"volunteer brief named {r.id} without sharing consent")
    for e in ctx.audit.events():
        if e.type == "tool_call" and e.policy_decision == "allow":
            if e.tool == "place_checkin_call" and ctx.mode != "live":
                problems.append(f"real call attempted in {ctx.mode} mode was allowed")
            if e.tool == "record_emergency_call" and e.actor.startswith("agent:"):
                problems.append("an agent recorded an emergency call")
    return problems
