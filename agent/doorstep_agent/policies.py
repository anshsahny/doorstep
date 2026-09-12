"""Cedar policy wiring (SPEC §8), built on the Strands Agents SDK `CedarAuthorization` handler.

- Policies live in `agent/policies/*.cedar` and are concatenated at load time.
- A Cedar schema is generated from the Strands tool specs plus the session fields the
  `context_enricher` supplies, so every policy is validated at startup: unknown actions, unknown
  context attributes and type errors all fail fast. (The SDK's own `tools=` auto-schema cannot be
  combined with enricher fields; see PROGRESS.md 2026-09-11 for the spike.)
- The enricher computes the SPEC §8 session values from the runtime context and the tool input,
  and leaves a copy in `invocation_state["_cedar_session"]` so the audit hook can record what
  each decision was based on.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from strands.types.tools import AgentTool
from strands.vended_interventions.cedar import CedarAuthorization

from .geo import haversine_km
from .runtime import RunContext, resolve_ref
from .store import NotFound

COORDINATOR_PRINCIPAL = {"type": "User", "id": "coordinator"}

# Session attributes the enricher always supplies, with their Cedar types.
SESSION_FIELDS: Mapping[str, str] = {
    "mode": "String",
    "role": "String",
    "channel": "String",
    "callee_allowlisted": "Bool",
    "callee_consented": "Bool",
    "attempts_last_hour": "Long",
    "local_hour": "Long",
    "alert_severity": "String",
    "volunteer_available": "Bool",
    "volunteer_distance_km": "Long",
    "family_consent": "Bool",
}

_JSON_TO_CEDAR = {"string": "String", "integer": "Long", "boolean": "Bool"}


class SchemaError(ValueError):
    pass


def load_policy_text(policies_dir: Path) -> str:
    files = sorted(policies_dir.glob("*.cedar"))
    if not files:
        raise FileNotFoundError(f"no .cedar files in {policies_dir}")
    return "\n\n".join(f"// ---- {f.name} ----\n{f.read_text(encoding='utf-8')}" for f in files)


def _cedar_type(prop: dict[str, Any], where: str) -> str:
    """JSON-schema property -> Cedar type. Floats and nested objects are refused on purpose."""
    if "anyOf" in prop:
        options = [o for o in prop["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            return _cedar_type(options[0], where)
        raise SchemaError(f"{where}: unions other than Optional are not supported")
    jtype = prop.get("type")
    if isinstance(jtype, list):
        non_null = [t for t in jtype if t != "null"]
        jtype = non_null[0] if len(non_null) == 1 else None
    if jtype in _JSON_TO_CEDAR:
        return _JSON_TO_CEDAR[jtype]
    if jtype == "array":
        return f"Set<{_cedar_type(prop.get('items', {'type': 'string'}), where)}>"
    raise SchemaError(f"{where}: unsupported JSON type {jtype!r} (use str, int, bool or list)")


def cedar_schema_for_tools(
    tools: Sequence[AgentTool], session_fields: Mapping[str, str] = SESSION_FIELDS
) -> str:
    """Generate a Cedar schema from Strands tool specs and the declared session fields."""
    lines = ["entity User;", "entity Resource;", "type SessionContext = {"]
    fields = {"hour_utc": "Long", "call_count": "Long", **session_fields}
    lines.append(",\n".join(f"  {name}: {ctype}" for name, ctype in fields.items()))
    lines.append("};")
    seen: set[str] = set()
    for tool in tools:
        spec = tool.tool_spec
        name = spec["name"]
        if name in seen:
            continue  # the same tool may be listed for several agents
        seen.add(name)
        schema = spec["inputSchema"]["json"]
        props: dict[str, Any] = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        attrs = []
        for prop_name, prop in props.items():
            opt = "" if prop_name in required else "?"
            attrs.append(f"{prop_name}{opt}: {_cedar_type(prop, f'{name}.{prop_name}')}")
        input_type = "{ " + ", ".join(attrs) + " }" if attrs else "{}"
        lines.append(
            f'action "{name}" appliesTo {{ principal: [User], resource: [Resource], '
            f"context: {{ input: {input_type}, session: SessionContext }} }};"
        )
    return "\n".join(lines) + "\n"


def build_context_enricher(ctx: RunContext) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Compute the SPEC §8 `context.session` values for one tool call."""

    def enrich(call: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = call["invocation_state"]
        tool_input: dict[str, Any] = call.get("tool_input") or {}

        resident = None
        rid = tool_input.get("resident_id")
        if isinstance(rid, str) and rid:
            try:
                resident = ctx.store.resident(rid)
            except NotFound:
                resident = None
        volunteer = None
        vid = tool_input.get("volunteer_id")
        if isinstance(vid, str) and vid:
            try:
                volunteer = ctx.store.volunteer(vid)
            except NotFound:
                volunteer = None

        allowlisted = False
        if resident is not None and resident.phone_ref:
            number = resolve_ref(resident.phone_ref)
            allowlisted = bool(number) and number in ctx.call_allowlist

        attempts_last_hour = 0
        if resident is not None:
            try:
                case = ctx.store.case(ctx.incident_id, resident.id)
                cutoff = ctx.clock.now() - timedelta(hours=1)
                attempts_last_hour = sum(1 for a in case.attempt_log if a.started_at >= cutoff)
            except NotFound:
                attempts_last_hour = 0

        distance_km = 9999
        if resident is not None and volunteer is not None:
            distance_km = int(
                math.floor(haversine_km(resident.lat, resident.lng, volunteer.lat, volunteer.lng))
            )

        session = {
            "mode": ctx.mode,
            "role": str(state.get("role", "agent")),
            "channel": ctx.channel,
            "callee_allowlisted": allowlisted,
            "callee_consented": bool(resident is not None and resident.consent.calls),
            "attempts_last_hour": attempts_last_hour,
            "local_hour": ctx.local_hour(),
            "alert_severity": ctx.alert_severity,
            "volunteer_available": bool(volunteer is not None and volunteer.available),
            "volunteer_distance_km": distance_km,
            "family_consent": bool(
                resident is not None and resident.consent.family and resident.family_contact_ref
            ),
        }
        state["_cedar_session"] = dict(session)
        return session

    return enrich


def build_cedar(
    ctx: RunContext,
    *,
    principal: Mapping[str, str] = COORDINATOR_PRINCIPAL,
    policies_dir: Path | None = None,
) -> CedarAuthorization:
    """A validated Cedar handler for one agent (handlers must not be shared between agents).

    The schema covers every Doorstep tool because the policy files name every action.
    """
    from .tools import ALL_TOOLS  # local import: tools.py must not depend on policies.py

    policies = load_policy_text(policies_dir or ctx.settings.policies_dir)
    schema = cedar_schema_for_tools(ALL_TOOLS)
    return CedarAuthorization(
        policies=policies,
        schema=schema,
        principal=dict(principal),
        context_enricher=build_context_enricher(ctx),
    )
