"""Every word a human reads on their phone, in one place (SPEC §4).

Two audiences, two different amounts of information, and the difference is the product:

* **The captain decides**, so their message carries enough to decide — who, where, what the
  resident said in their own words, what has already been done, and what the choices are.
* **The volunteer knocks**, so their message carries only what changes the knock — a door, a
  reason, and the roster notes the team already agreed to share. Not the risk score, not another
  resident, not a contact reference. A brief that travels further than the decision it serves is
  a leak, however well meant.

The line between them is not "health facts vs the rest", it is **standing facts vs today's
call**. A roster note ("needs power for a medical device", "hard of hearing", "often leaves the
phone off the hook") was written by the team for this purpose and is gated on
`consent.share_with_volunteer`; it changes how a volunteer knocks and what they do if nobody
answers, so it goes. What the resident said on today's call does not: the quote is the captain's
evidence for a decision, not a detail a neighbour needs on their phone to reach a door.

Keeping the strings here rather than inside the tools means the minimal-disclosure rule is a
thing you can read, and a thing `tests/test_messages.py` can hold to over the whole roster.
"""

from __future__ import annotations

from .models import Decision, Resident, ResidentCase
from .runtime import RunContext

FICTIONAL = "fictional drill data"

_SEVERITY_MARK = {
    "doorstep-urgent-red-flag": "🔴",
    "doorstep-high-risk-no-answer": "🟠",
    "doorstep-unmet-need": "🟠",
    "doorstep-approve-door-knock": "🟠",
    "doorstep-borderline-activation": "🔵",
}


VOLUNTEER_UPDATE = "doorstep-volunteer-update"


def decision_body(ctx: RunContext, decision: Decision) -> str:
    """The text for a decision, addressed to whoever it is for.

    One function so a channel never has to know which audience it is talking to, and so the two
    disclosure levels cannot be swapped by a caller passing the wrong renderer.
    """
    if decision.name == VOLUNTEER_UPDATE and decision.resident_id:
        resident = ctx.store.resident(decision.resident_id)
        case = ctx.store.case(ctx.incident_id, decision.resident_id)
        volunteer = ctx.store.volunteer(decision.audience)
        return volunteer_task(
            ctx, resident, case, decision.reason, volunteer_name=volunteer.name.split()[0]
        )
    return captain_message(ctx, decision)


def _who(resident: Resident) -> str:
    """How a resident is named to the captain: enough to picture the door."""
    alone = "lives alone" if resident.lives_alone else "does not live alone"
    return f"{resident.first_name}, {resident.age_band}, {alone} — {resident.address_label}."


def captain_message(ctx: RunContext, decision: Decision) -> str:
    """The decision as the captain sees it, options excluded (the channel renders those)."""
    mark = _SEVERITY_MARK.get(decision.name, "🔵")
    # The hazard names itself from the active profile; nothing here knows which one is running.
    header = f"{mark} Doorstep · {ctx.org.name} · {ctx.profile.display_name.lower()} check-in"
    if not decision.resident_id:
        return "\n\n".join([header, decision.reason, _footer(ctx, decision)])

    resident = ctx.store.resident(decision.resident_id)
    case = ctx.store.case(ctx.incident_id, decision.resident_id)
    body = [_who(resident), decision.reason]

    result = case.latest_result
    if result and result.key_quote:
        # "They" always: the roster records no pronouns, and guessing one from a name is how a
        # message that is meant to help ends up misgendering a neighbour.
        body.append(f'They said: "{result.key_quote}"')
    if decision.name == "doorstep-urgent-red-flag":
        body.append("They were told to call 911.")
        body.append("Doorstep does not call emergency services. Your call:")
    elif decision.name in ("doorstep-high-risk-no-answer", "doorstep-approve-door-knock"):
        body.append(_standing_facts(resident))
        body.append("Approve a door-knock?")
    else:
        body.append("Your call:")
    return "\n\n".join([header, "\n".join(line for line in body if line), _footer(ctx, decision)])


def _standing_facts(resident: Resident) -> str:
    """The few facts that explain why this resident is high risk, and nothing beyond them.

    `power_dependent` is the one health-adjacent flag Doorstep keeps (CLAUDE.md: no diagnoses,
    no medications) and it belongs here: it is the reason the captain should hurry.
    """
    facts = []
    if not resident.has_ac:
        facts.append("no AC")
    if resident.power_dependent:
        facts.append("needs power for a medical device")
    if resident.mobility_limited:
        facts.append("limited mobility")
    if not facts:
        return ""
    # Upper-case the first letter only; `.capitalize()` would turn "no AC" into "No ac".
    sentence = ", ".join(facts)
    return sentence[0].upper() + sentence[1:] + "."


def _footer(ctx: RunContext, decision: Decision) -> str:
    when = decision.created_at.strftime("%H:%M")
    return (
        f"{decision.id} · {when} · {FICTIONAL}" if ctx.mode != "live" else f"{decision.id} · {when}"
    )


def volunteer_task(
    ctx: RunContext, resident: Resident, case: ResidentCase, why: str, volunteer_name: str = ""
) -> str:
    """The task as the assigned volunteer sees it. Deliberately the smaller message.

    What is missing is the point: no quote from today's call, no risk score, no family contact,
    no contact reference, no other resident. Consented roster notes do travel — see the module
    docstring for where that line sits and why.
    """
    first = resident.first_name if resident.consent.share_with_volunteer else "the resident"
    lines = [
        f"Doorstep · task for {volunteer_name}" if volunteer_name else "Doorstep · task",
        "",
        f"Please check on {first} at {resident.address_label}.",
        why if why.endswith(".") else f"{why}.",
    ]
    if resident.consent.share_with_volunteer:
        lines.extend(note for note in resident.notes if note)
    if case.latest_result and case.latest_result.status == "URGENT":
        lines.append("If they need help, call 911 yourself and tell the captain.")
    else:
        lines.append("If nobody answers, tell the captain.")
    return "\n".join(lines)


def already_answered(decision: Decision) -> str:
    when = decision.responded_at.strftime("%H:%M") if decision.responded_at else "earlier"
    chosen = decision.option(decision.response or "")
    return (
        f'✅ {decision.id} — already answered at {when}: "{chosen.label if chosen else "?"}". '
        "Nothing was sent twice."
    )


def answered(decision: Decision) -> str:
    when = decision.responded_at.strftime("%H:%M") if decision.responded_at else "now"
    chosen = decision.option(decision.response or "")
    return f'✅ {decision.id} — you chose "{chosen.label if chosen else "?"}" at {when}.'


def expired(decision: Decision) -> str:
    when = decision.expires_at.strftime("%H:%M") if decision.expires_at else "earlier"
    return (
        f"⏱ {decision.id} — this expired at {when} and nobody was sent. "
        "The case is still open with the captain."
    )


def forbidden(ctx: RunContext) -> str:
    return f"🚫 You're not on the {ctx.org.name} list for this decision."
