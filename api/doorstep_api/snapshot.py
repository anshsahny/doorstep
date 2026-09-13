"""What the dashboard sees of one incident, and the incident report. Pure functions over rows.

Everything here is shaped for display and minimal on purpose: first names, unit and street, the
consented standing notes a volunteer would also get, today's check-in, decisions and the audit
trail. Never a phone reference, a family contact reference or a Telegram chat. Any value that
looks like a phone number is masked on the way out, whatever field it turned up in.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# Phone shapes only: +E.164, or ten digits written as a phone number. Ids and timestamps
# ("sandbox-20260913-144916-8ad64a", "2026-09-13T12:00:21Z") contain long digit runs and must
# pass through untouched.
PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{10,15}(?!\d)"
    r"|(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\w-])"
    r"|\d{10}(?![\w-]))"
)
REACHED = {"OK", "NEEDS_HELP", "URGENT", "ASSIGNED", "ESCALATED", "RESOLVED"}
HUMAN_ACTORS = ("captain:", "volunteer:")
MAX_EVENTS = 400


def mask(value: Any) -> Any:
    if isinstance(value, str):
        return PHONE.sub("[number]", value)
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    return value


def _at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds(start: str | None, end: str | None) -> float | None:
    a, b = _at(start), _at(end)
    return round((b - a).total_seconds(), 1) if a and b else None


# --- static roster ------------------------------------------------------------------------


def resident_view(doc: dict[str, Any]) -> dict[str, Any]:
    consent = doc.get("consent") or {}
    return {
        "id": doc["id"],
        "first_name": doc.get("first_name", ""),
        "unit": doc.get("unit"),
        "building": doc.get("building", ""),
        "address_label": doc.get("address_label", ""),
        "age_band": doc.get("age_band", ""),
        "lives_alone": bool(doc.get("lives_alone")),
        "has_ac": bool(doc.get("has_ac")),
        "power_dependent": bool(doc.get("power_dependent")),
        "mobility_limited": bool(doc.get("mobility_limited")),
        "language": doc.get("language", "en"),
        # The same line as a volunteer brief: consented standing facts only.
        "notes": list(doc.get("notes") or []) if consent.get("share_with_volunteer") else [],
        "lat": doc.get("lat"),
        "lng": doc.get("lng"),
        "fictional": True,
    }


def volunteer_view(doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": doc["id"], "name": doc.get("name", ""), "role": doc.get("role", "volunteer")}


# --- incident rows --------------------------------------------------------------------------


def incident_view(doc: dict[str, Any]) -> dict[str, Any]:
    alert = doc.get("alert") or {}
    options = doc.get("run_options") or {}
    return {
        "id": doc["id"],
        "mode": doc.get("mode"),
        "status": doc.get("status"),
        "profile_id": doc.get("profile_id"),
        "started_at": doc.get("started_at"),
        "resident_ids": list(doc.get("resident_ids") or []),
        "voice_residents": list(options.get("voice_residents") or []),
        "alert": {
            k: alert.get(k, "")
            for k in ("event", "severity", "headline", "area_desc", "onset", "expires", "sender")
        },
        "assessment": {
            k: (doc.get("assessment") or {}).get(k) for k in ("severity", "window", "rationale")
        },
    }


def case_view(doc: dict[str, Any]) -> dict[str, Any]:
    results = doc.get("results") or []
    latest = results[-1] if results else None
    return {
        "resident_id": doc["resident_id"],
        "state": doc.get("state"),
        "attempts": doc.get("attempts", 0),
        "risk": {k: (doc.get("risk") or {}).get(k) for k in ("points", "wave", "factors")},
        "result": None
        if latest is None
        else {
            k: latest.get(k)
            for k in (
                "status",
                "needs",
                "red_flags",
                "summary",
                "key_quote",
                "flagged_mid_call",
                "confidence",
                "language",
            )
        }
        | {"backstop": latest.get("backstop")},
        "attempt_log": [
            {
                k: a.get(k)
                for k in (
                    "attempt",
                    "channel",
                    "started_at",
                    "ended_at",
                    "answered",
                    "transcript",
                    "answers",
                )
            }
            for a in doc.get("attempt_log") or []
        ],
        "assigned_volunteer": doc.get("assigned_volunteer"),
        "outcome": doc.get("outcome"),
        "updated_at": doc.get("updated_at"),
        "history": doc.get("history") or [],
    }


def decision_view(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "resident_id": doc.get("resident_id"),
        "name": doc.get("name"),
        "reason": doc.get("reason", ""),
        "options": [{"id": o["id"], "label": o["label"]} for o in doc.get("options") or []],
        "status": doc.get("status"),
        "audience": doc.get("audience", ""),
        "created_at": doc.get("created_at"),
        "expires_at": doc.get("expires_at"),
        "responder": doc.get("responder"),
        "response": doc.get("response"),
        "responded_at": doc.get("responded_at"),
        "applied_at": doc.get("applied_at"),
    }


def event_view(doc: dict[str, Any]) -> dict[str, Any]:
    data = doc.get("data") or {}
    return {
        "seq": doc["seq"],
        "at": doc.get("at"),
        "actor": doc.get("actor", ""),
        "type": doc.get("type", ""),
        "resident_id": doc.get("resident_id"),
        "tool": doc.get("tool"),
        "policy_decision": doc.get("policy_decision"),
        "reason": (doc.get("reason") or "")[:400],
        "rationale": (doc.get("rationale") or "")[:400],
        "input_summary": (doc.get("input_summary") or "")[:300],
        "by": data.get("by"),
        "source": data.get("source"),
        "outcome": data.get("outcome"),
    }


def build(
    *,
    incident: dict[str, Any],
    cases: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    residents: list[dict[str, Any]] | None = None,
    volunteers: list[dict[str, Any]] | None = None,
    now: str = "",
) -> dict[str, Any]:
    events = sorted(events, key=lambda e: e["seq"])[:MAX_EVENTS]
    body: dict[str, Any] = {
        "incident": incident_view(incident),
        "cases": [case_view(c) for c in sorted(cases, key=lambda c: c["resident_id"])],
        "decisions": [decision_view(d) for d in sorted(decisions, key=lambda d: d["id"])],
        "events": [event_view(e) for e in events],
        "last_seq": events[-1]["seq"] if events else None,
        "server_time": now,
        "fictional": True,
    }
    wanted = set(incident.get("resident_ids") or [])
    if residents is not None:
        body["residents"] = [resident_view(r) for r in residents if r["id"] in wanted]
    if volunteers is not None:
        body["volunteers"] = [volunteer_view(v) for v in volunteers]
    return mask(body)


# --- the report -----------------------------------------------------------------------------


def report(
    *,
    incident: dict[str, Any],
    cases: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    org: dict[str, Any],
) -> dict[str, Any]:
    """The incident report: outcomes, time to reach everyone, people vs automation, the baseline."""
    started = incident.get("started_at")
    first_call: str | None = None
    reached_at: list[str] = []
    outcomes: dict[str, int] = {}
    urgent: list[dict[str, Any]] = []
    for case in cases:
        history = case.get("history") or []
        calls = [h["at"] for h in history if h.get("to_state") == "CALLING"]
        if calls and (first_call is None or calls[0] < first_call):
            first_call = calls[0]
        reach = next((h["at"] for h in history if h.get("to_state") in REACHED), None)
        if reach:
            reached_at.append(reach)
        results = case.get("results") or []
        status = results[-1].get("status") if results else "NOT_REACHED"
        outcomes[status] = outcomes.get(status, 0) + 1
        if status == "URGENT":
            page = min(
                (
                    d.get("created_at")
                    for d in decisions
                    if d.get("resident_id") == case["resident_id"]
                    and d.get("name") == "doorstep-urgent-red-flag"
                ),
                default=None,
            )
            attempt = (case.get("attempt_log") or [{}])[-1]
            urgent.append(
                {
                    "resident_id": case["resident_id"],
                    "state": case.get("state"),
                    "call_to_page_seconds": _seconds(attempt.get("started_at"), page),
                    "flagged_mid_call": bool(results[-1].get("flagged_mid_call")),
                }
            )

    answered = [d for d in decisions if d.get("status") == "answered"]
    human = [d for d in answered if str(d.get("responder") or "").startswith(HUMAN_ACTORS)]
    by_source: dict[str, int] = {}
    for e in events:
        if e.get("type") == "decision" and str(e.get("actor", "")).startswith(HUMAN_ACTORS):
            source = (e.get("data") or {}).get("source")
            if source:
                by_source[source] = by_source.get(source, 0) + 1
    automated = [
        e
        for e in events
        if e.get("type") == "tool_call"
        and str(e.get("actor", "")).startswith("agent:")
        and (e.get("data") or {}).get("status") == "success"
    ]
    denials = [
        e for e in events if e.get("policy_decision") == "deny" and e.get("type") == "policy"
    ]

    baseline = org.get("phone_tree_baseline") or {}
    minutes_per_call = float(baseline.get("minutes_per_call", 4))
    callers = max(float(baseline.get("volunteers_calling", 1)), 1.0)
    covered = len(incident.get("resident_ids") or cases)
    all_reached = len(reached_at) == covered and covered > 0
    last_reach = max(reached_at) if reached_at else None
    kinds: dict[str, int] = {}
    for m in messages:
        kinds[m.get("kind", "other")] = kinds.get(m.get("kind", "other"), 0) + 1

    return {
        "incident_id": incident["id"],
        "mode": incident.get("mode"),
        "residents": covered,
        "reached": len(reached_at),
        "all_reached": all_reached,
        "not_reached": sorted(
            c["resident_id"]
            for c in cases
            if not any(h.get("to_state") in REACHED for h in c.get("history") or [])
        ),
        "outcomes": outcomes,
        "seconds_to_first_call": _seconds(started, first_call),
        "seconds_to_reach_everyone": _seconds(started, last_reach) if all_reached else None,
        "seconds_to_last_reached": _seconds(started, last_reach),
        "urgent": urgent,
        "decisions": {
            "raised": len([d for d in decisions if d.get("status") != "draft"]),
            "answered_by_people": len(human),
            "answered_automatically": len(answered) - len(human),
            "waiting": len([d for d in decisions if d.get("status") == "pending"]),
            "expired": len([d for d in decisions if d.get("status") == "expired"]),
            "by_channel": by_source,
        },
        "automated_actions": len(automated),
        "policy_denials": [event_view(e) for e in denials],
        "messages": kinds,
        "phone_tree": {
            "residents": covered,
            "minutes_per_call": minutes_per_call,
            "volunteers_calling": int(callers),
            "minutes": round(covered * minutes_per_call / callers, 1),
        },
        "fictional": True,
    }
