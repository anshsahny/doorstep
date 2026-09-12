"""The one path every human decision takes, whatever channel it arrived on (PLAN Phase 2 task 3+5).

Telegram today, the dashboard in Phase 5, the terminal board in a drill: each of them only
renders a `Decision` and turns a tap into `(decision_id, option_id, Responder)`. All of the
judgement — who may answer, whether this was already answered, whether it expired, what happens
next — lives here, so a second channel adds no new rules and can get none of them wrong.

Two properties this module exists to guarantee:

* **Exactly once.** A decision can leave `pending` only once. The status gate runs before
  anything touches the agent, so a second tap, or a tap after a timeout, never reaches Strands
  and can never re-run a tool. The human is told which of those happened.
* **Identity.** A responder is resolved against the roster and checked against the one person the
  decision was addressed to. A Telegram user who is not on the roster can answer nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from .models import CaseState, Decision, DecisionOption, ResidentCase, Volunteer
from .runtime import RunContext, resolve_ref
from .state_machine import transition
from .store import NotFound

# Which roster role may answer each kind of decision. SPEC §4 says the captain is interrupted and
# volunteers get tasks; this is that sentence made enforceable.
CAPTAIN_DECISIONS = (
    "doorstep-urgent-red-flag",
    "doorstep-high-risk-no-answer",
    "doorstep-unmet-need",
    "doorstep-approve-door-knock",
)
VOLUNTEER_DECISIONS = ("doorstep-volunteer-update",)

OutcomeKind = Literal[
    "applied", "already_answered", "expired", "forbidden", "unknown_option", "not_found"
]


@dataclass(frozen=True)
class Responder:
    """Who is answering, as the channel knows them.

    `external_id` is a Telegram chat id, a dashboard session subject, or "auto-approve" in an
    unattended drill. Resolving it to a person is this module's job, not the channel's.
    """

    source: Literal["telegram", "web", "drill"]
    external_id: str


@dataclass(frozen=True)
class ResolvedResponder:
    member: Volunteer
    responder: Responder

    @property
    def actor(self) -> str:
        return f"{self.member.role}:{self.member.id}"


@dataclass(frozen=True)
class DecisionOutcome:
    """What happened, and the sentence to show the human who tapped."""

    kind: OutcomeKind
    message: str
    decision: Decision | None = None
    detail: str = ""

    @property
    def applied(self) -> bool:
        return self.kind == "applied"


def allowed_role(name: str) -> str:
    return "volunteer" if name in VOLUNTEER_DECISIONS else "captain"


def resolve_responder(
    ctx: RunContext, responder: Responder, *, addressed_to: str = ""
) -> ResolvedResponder | None:
    """Map a channel identity to a roster member, or None if they are not on the roster.

    A single Telegram account may stand in for more than one roster member during testing (one
    phone playing captain and volunteer). When that happens the member the message was addressed
    to wins, so the test exercises the real role check rather than whichever record sorted first.
    """
    if responder.source == "drill":
        return None
    candidates = [
        v
        for v in ctx.store.volunteers()
        if resolve_ref(v.telegram_chat_id_ref) == responder.external_id
    ]
    if not candidates:
        return None
    preferred = next((v for v in candidates if v.id == addressed_to), candidates[0])
    return ResolvedResponder(member=preferred, responder=responder)


def authorize(ctx: RunContext, decision: Decision, responder: Responder) -> ResolvedResponder | str:
    """Resolve and check the responder, or return a plain-language refusal."""
    resolved = resolve_responder(ctx, responder, addressed_to=decision.audience)
    if resolved is None:
        return "not on the Juniper Court list"
    if decision.audience and resolved.member.id != decision.audience:
        return f"addressed to {decision.audience}, not {resolved.member.id}"
    required = allowed_role(decision.name)
    if resolved.member.role != required:
        return f"needs the {required}; {resolved.member.id} is a {resolved.member.role}"
    return resolved


def expires_at(ctx: RunContext, *, minutes: float | None = None) -> datetime:
    """When a decision raised now stops being answerable — in **real** time, never compressed.

    Drill time compression exists to make the agent's own timers (retry intervals, re-checks)
    play out quickly. A human's thinking time is not one of those: the captain reading the
    message is a real person at real speed. Compressing this turned a 15-minute deadline into 30
    seconds, and a live drill expired a decision seven seconds before the captain's thumb landed
    while eight of them arrived at once.
    """
    ttl = ctx.settings.decision_ttl_minutes if minutes is None else minutes
    return ctx.clock.now() + timedelta(minutes=ttl)


def expire_due_decisions(ctx: RunContext) -> list[Decision]:
    """Expire every pending decision past its deadline.

    Expiry never decides anything on a human's behalf: the case stays open and escalated.
    """
    now = ctx.clock.now()
    expired: list[Decision] = []
    for decision in ctx.store.decisions(ctx.incident_id, status="pending"):
        if decision.expires_at is None or now < decision.expires_at:
            continue
        decision.status = "expired"
        ctx.store.save_decision(decision)
        ctx.audit.record(
            actor="system:decisions",
            type="decision",
            resident_id=decision.resident_id,
            reason=(
                f"{decision.id} [{decision.name}] expired unanswered; "
                "nothing was sent and the case stays open"
            ),
            data={"decision_id": decision.id, "audience": decision.audience},
        )
        expired.append(decision)
    return expired


def upsert_decision(
    ctx: RunContext,
    *,
    tool_use_id: str,
    resident_id: str | None,
    name: str,
    reason: str,
    options: list[DecisionOption],
    audience: str,
) -> Decision:
    """Find or create the decision raised by one tool use.

    Keyed on `tool_use_id`, not on a counter, because a tool that raises an interrupt re-runs
    from the top when it resumes (Spike A). Everything a tool does before its `interrupt()` call
    therefore happens twice, and this is the function that makes that harmless: the second run
    finds the record the first one created and changes nothing.
    """
    existing = ctx.store.decision_for_tool_use(ctx.incident_id, tool_use_id)
    if existing is not None:
        return existing

    # Allocated by the store, not counted: tool bodies run in threads, so two concurrent
    # dispatches counting the same list would both mint the same id.
    number = ctx.store.allocate(ctx.incident_id, "dec")
    decision = Decision(
        id=f"dec-{number:03d}",
        incident_id=ctx.incident_id,
        resident_id=resident_id,
        name=name,
        reason=reason,
        options=options,
        audience=audience,
        tool_use_id=tool_use_id,
        status="draft",
    )
    ctx.store.save_decision(decision)
    ctx.audit.record(
        actor="system:decisions",
        type="decision",
        resident_id=resident_id,
        reason=f"{name}: {reason}",
        data={
            "decision_id": decision.id,
            "audience": audience,
            "options": [o.label for o in options],
        },
    )
    return decision


def _interrupt_payload(decision: Decision, option: DecisionOption, actor: str) -> dict[str, Any]:
    """What the paused tool receives back from its `interrupt()` call. Must be JSON-serializable."""
    return {
        "decision_id": decision.id,
        "option_id": option.id,
        "label": option.label,
        "action": option.action,
        "args": dict(option.args),
        "responder": actor,
    }


async def respond_to_decision(
    ctx: RunContext,
    decision_id: str,
    option_id: str,
    responder: Responder,
    *,
    actor_override: str = "",
) -> DecisionOutcome:
    """Answer one decision. The only way a human choice ever enters the system.

    `actor_override` exists for the unattended drill's simulated captain, which has no roster
    identity; every real channel passes a `Responder` and goes through the identity check.
    """
    try:
        decision = ctx.store.decision(decision_id)
    except NotFound:
        return DecisionOutcome("not_found", "That decision is not on this incident.")

    # The idempotency gate. It sits before identity and before the agent on purpose: answering
    # twice must be cheap and safe even when the second tap is a stranger's.
    closed = _closed_outcome(decision)
    if closed is not None:
        return closed

    if actor_override:
        actor = actor_override
    else:
        checked = authorize(ctx, decision, responder)
        if isinstance(checked, str):
            ctx.audit.record(
                actor="system:decisions",
                type="decision",
                resident_id=decision.resident_id,
                policy_decision="deny",
                reason=f"{decision.id} answer refused: {checked}",
                data={"by": "code", "source": responder.source, "decision_id": decision.id},
            )
            return DecisionOutcome(
                "forbidden",
                "You're not on the Juniper Court list for this decision.",
                decision,
                detail=checked,
            )
        actor = checked.actor

    option = decision.option(option_id)
    if option is None:
        return DecisionOutcome("unknown_option", "That option is no longer on offer.", decision)

    # Claim the decision before resuming. The store makes the claim atomic (a conditional write on
    # DynamoDB), so of two taps racing here — in two threads or two processes — exactly one wins;
    # the other is told what the winner chose.
    claimed = ctx.store.claim_decision(
        decision.id, responder=actor, response=option.id, responded_at=ctx.clock.now()
    )
    if claimed is None:
        lost = _closed_outcome(ctx.store.decision(decision_id))
        return lost or DecisionOutcome("not_found", "That decision is not ready to answer yet.")
    decision = claimed
    ctx.audit.record(
        actor=actor,
        type="decision",
        resident_id=decision.resident_id,
        reason=f"{decision.id} [{decision.name}] -> {option.label}",
        data={"decision_id": decision.id, "option_id": option.id, "source": responder.source},
    )

    detail = await _apply(ctx, decision, option, actor)
    decision.applied_at = ctx.clock.now()
    ctx.store.save_decision(decision)
    return DecisionOutcome("applied", f'You chose "{option.label}".', decision, detail=detail)


def _closed_outcome(decision: Decision) -> DecisionOutcome | None:
    """What to tell a human who taps a decision that can no longer be answered, or None."""
    if decision.status == "answered":
        when = decision.responded_at.strftime("%H:%M") if decision.responded_at else "earlier"
        chosen = decision.option(decision.response or "")
        label = chosen.label if chosen else decision.response
        return DecisionOutcome(
            "already_answered",
            f'Already answered at {when}: "{label}". Nothing was sent twice.',
            decision,
        )
    if decision.status == "expired":
        return DecisionOutcome(
            "expired",
            "This expired before anyone answered, so nothing was sent. The case is still open.",
            decision,
        )
    if decision.status != "pending":
        return DecisionOutcome("not_found", "That decision is not ready to answer yet.", decision)
    return None


def redrive_unapplied(ctx: RunContext, *, older_than_seconds: float = 60.0) -> list[str]:
    """Carry out answered decisions whose process stopped between the claim and the effect.

    Only the deterministic half is re-run: it checks the case state first, so it is a no-op for
    anything already done. Returns one line per decision it touched.
    """
    now = ctx.clock.now()
    lines: list[str] = []
    for decision in ctx.store.decisions(ctx.incident_id, status="answered"):
        if decision.applied_at is not None or decision.responded_at is None:
            continue
        if (now - decision.responded_at).total_seconds() < older_than_seconds:
            continue
        option = decision.option(decision.response or "")
        if option is None:
            continue
        settled = _apply_directly(ctx, decision, option, decision.responder or "system:redrive")
        decision.applied_at = now
        ctx.store.save_decision(decision)
        ctx.audit.record(
            actor="system:decisions",
            type="decision",
            resident_id=decision.resident_id,
            reason=f"{decision.id} was answered but not carried out; re-applied: {settled}",
            data={"decision_id": decision.id},
        )
        lines.append(f"{decision.id}: {settled}")
    return lines


async def _apply(ctx: RunContext, decision: Decision, option: DecisionOption, actor: str) -> str:
    """Carry out the choice: resume the paused agent, then guarantee the deterministic part.

    The agent is resumed so it can do the work that needs judgement — writing the brief, picking
    the message. But the state change the captain actually chose is not left to it: a live drill
    showed the model reliably closing one case after "I'm handling it" and silently forgetting
    another. `_apply_directly` runs afterwards and is idempotent, so the model gets its turn and
    the captain's choice happens either way. The model proposes; deterministic code decides.
    """
    detail = ""
    if decision.interrupt_id and decision.session_id:
        from .agents.dispatcher import resume_dispatch  # local: dispatcher imports this module

        role = allowed_role(decision.name)
        detail = await resume_dispatch(
            ctx,
            decision,
            _interrupt_payload(decision, option, actor),
            role=role,
            actor=actor,
        )
    settled = _apply_directly(ctx, decision, option, actor)
    return f"{detail}; {settled}" if detail and settled != "recorded" else detail or settled


def _apply_directly(ctx: RunContext, decision: Decision, option: DecisionOption, actor: str) -> str:
    """The deterministic half of a choice, safe to run whether or not an agent also ran.

    Every branch checks the current state first, so this is idempotent: called after a resume in
    which the model already closed the case, it does nothing and says so.
    """
    resident_id = decision.resident_id
    if not resident_id:
        return "recorded"
    try:
        case = ctx.store.case(ctx.incident_id, resident_id)
    except NotFound:
        return "recorded"

    if option.action == "resolve" and case.state != CaseState.RESOLVED:
        transition(case, CaseState.RESOLVED, reason=f"{actor}: {option.label}")
        case.outcome = str(option.args.get("outcome") or "captain handling")
        ctx.store.save_case(case)
        return f"{resident_id} closed: {case.outcome}"
    if option.action == "escalate" and case.state != CaseState.ESCALATED:
        transition(case, CaseState.ESCALATED, reason=f"{actor}: {option.label}")
        ctx.store.save_case(case)
        return f"{resident_id} escalated to the captain"
    if option.action == "assign_volunteer":
        return _send_the_volunteer(ctx, decision, option, actor, case)
    if option.action == "notify_family":
        return _tell_the_family(ctx, decision, option, actor)
    if option.action == "acknowledge":
        ctx.audit.record(
            actor=actor,
            type="decision",
            resident_id=resident_id,
            reason=f"{option.label} ({decision.id})",
        )
        return f"{resident_id}: {option.label}"
    return "recorded"


def _send_the_volunteer(
    ctx: RunContext, decision: Decision, option: DecisionOption, actor: str, case: ResidentCase
) -> str:
    """Send the volunteer the captain picked, if the model has not already done it.

    This is the branch a live drill was missing: the captain chose "Send Sam" for two urgent
    residents, the model never called the tool, and nobody was sent. The captain's tap is the
    approval, so this deliberately bypasses `ApprovalHook` — asking them to approve what they
    just chose is the double-ask that SPEC §4a forbids.
    """
    from .tools import send_volunteer_task  # local: tools imports this module

    volunteer_id = str(option.args.get("volunteer_id") or "")
    if case.state == CaseState.ASSIGNED and case.assigned_volunteer:
        return f"{case.resident_id} already assigned to {case.assigned_volunteer}"
    try:
        resident = ctx.store.resident(str(decision.resident_id))
        volunteer = ctx.store.volunteer(volunteer_id)
    except NotFound:
        ctx.audit.record(
            actor=actor,
            type="decision",
            resident_id=decision.resident_id,
            policy_decision="deny",
            reason=f"{decision.id}: cannot send {volunteer_id or '(nobody)'}; not on the roster",
            data={"by": "code"},
        )
        return f"could not send {volunteer_id}: not on the roster"
    if case.state not in (CaseState.NEEDS_HELP, CaseState.URGENT, CaseState.ESCALATED):
        return f"{case.resident_id} is {case.state}; no visit sent"
    return send_volunteer_task(
        ctx,
        resident,
        volunteer,
        decision.reason,
        include_brief=True,
        tool_use_id=f"decision:{decision.id}",
    )


def _tell_the_family(
    ctx: RunContext, decision: Decision, option: DecisionOption, actor: str
) -> str:
    """Tell the family the captain chose to tell, if the model has not already done it.

    The branch the Gate 3 cloud drill found missing: the captain tapped "Call the family contact"
    and only the model's goodwill stood between that tap and the family hearing anything. The
    key is the decision, the same one the tool uses when it runs inside this resume.
    """
    from .tools import send_family_notice  # local: tools imports this module

    try:
        resident = ctx.store.resident(str(decision.resident_id))
    except NotFound:
        return "recorded"
    reason = str(option.args.get("reason") or decision.reason)
    return send_family_notice(
        ctx, resident, reason, once_key=f"decision:{decision.id}", actor=actor
    )


def withdraw_undeliverable_task(ctx: RunContext, decision: Decision) -> None:
    """A volunteer task that could not reach its volunteer goes back to the captain.

    Found in the Gate 3 Telegram drill: the captain chose "Send Sam", Sam had no chat id, the
    task was (rightly) not sent — and the case then sat ASSIGNED to someone who never heard about
    it, waiting for a reply that could not come. The task is withdrawn and the case is escalated
    again, so it is back in front of the captain with the reason on the record.
    """
    if decision.status != "pending":
        return
    decision.status = "expired"
    ctx.store.save_decision(decision)
    back_to_captain = False
    if decision.resident_id:
        try:
            case = ctx.store.case(ctx.incident_id, decision.resident_id)
        except NotFound:
            case = None
        if (
            case is not None
            and case.state == CaseState.ASSIGNED
            and case.assigned_volunteer == decision.audience
        ):
            transition(
                case,
                CaseState.ESCALATED,
                reason=f"{decision.audience} could not be reached; back to the captain",
            )
            case.assigned_volunteer = None
            ctx.store.save_case(case)
            back_to_captain = True
    ctx.audit.record(
        actor="system:decisions",
        type="decision",
        resident_id=decision.resident_id,
        reason=(
            f"{decision.id}: task could not be delivered to {decision.audience}; withdrawn"
            + ("; the case is escalated to the captain again" if back_to_captain else "")
        ),
        data={"decision_id": decision.id, "audience": decision.audience},
    )


def stamp_pending(
    ctx: RunContext,
    decision: Decision,
    *,
    interrupt_id: str,
    session: str,
) -> Decision:
    """Make a raised decision answerable: attach the interrupt and session, then open it.

    Nothing is delivered before this runs, so a button can never exist for a decision that
    cannot yet be answered.
    """
    decision.interrupt_id = interrupt_id
    decision.session_id = session
    if decision.status == "draft":
        decision.status = "pending"
        decision.expires_at = expires_at(ctx)
    ctx.store.save_decision(decision)
    return decision
