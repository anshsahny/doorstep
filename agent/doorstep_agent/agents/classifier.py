"""classifier: a structured CheckinResult from a transcript, then the deterministic backstop.

Order of authority (SPEC §6.1): the model proposes a classification; a mid-call `flag_urgent`
from the check-in agent raises it to URGENT; the phrase backstop raises it to URGENT. Nothing in
this module can lower a status. Every disagreement is written to the audit log.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from strands import Agent

from ..backstop import apply_backstop
from ..guards import ModelCallGuard
from ..models import (
    CheckinAttempt,
    CheckinResult,
    CheckinStatus,
    Resident,
    as_str_list,
    as_text,
)
from ..runtime import RunContext
from ._common import bullet_list, make_model

AGENT_NAME = "classifier"


class Classification(BaseModel):
    """What the model returns. Vocabularies are validated against the profile afterwards."""

    status: Literal["OK", "NEEDS_HELP", "URGENT", "UNCLEAR"] = Field(
        description="URGENT for any red flag or stated emergency; NEEDS_HELP for an unmet need; "
        "OK when they are fine; UNCLEAR when you cannot tell"
    )
    needs: list[str] = Field(default_factory=list, description="Need ids from the allowed list")
    red_flags: list[str] = Field(
        default_factory=list, description="Red-flag category ids from the allowed list"
    )
    indoor_temp_hint: str | None = Field(
        default=None, description="What they said about conditions at home, briefly"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0 to 1")
    key_quote: str = Field(description="Their most telling words, verbatim, or empty")
    summary: str = Field(description="One sentence for the captain, no medical detail")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> object:
        return value.strip().upper().replace(" ", "_") if isinstance(value, str) else value

    @field_validator("needs", "red_flags", mode="before")
    @classmethod
    def _lists(cls, value: object) -> object:
        return as_str_list(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> object:
        try:
            return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5

    @field_validator("key_quote", "summary", "indoor_temp_hint", mode="before")
    @classmethod
    def _text(cls, value: object) -> object:
        return as_text(value)


def system_prompt(ctx: RunContext) -> str:
    profile = ctx.profile
    needs = bullet_list([f"{n.id}: {n.label}" for n in profile.needs])
    flags = bullet_list([f"{r.category}: {r.label}" for r in profile.red_flags])
    return (
        f"You classify neighbour check-in transcripts during a '{profile.display_name}' "
        "incident. Built with Strands Agents. Read the whole transcript and the recorded "
        "answers.\n\n"
        "Status rules:\n"
        "- URGENT if the resident mentions any red flag below, even in passing or understated, "
        "or says it is an emergency.\n"
        "- NEEDS_HELP if they are safe now but lack something on the needs list, or ask for a "
        "visit or a ride.\n"
        "- OK if they are fine and have what they need.\n"
        "- UNCLEAR if the conversation did not establish how they are.\n\n"
        "Allowed needs (use these ids only):\n"
        f"{needs}\n\n"
        "Allowed red-flag categories (use these ids only):\n"
        f"{flags}\n\n"
        "Be conservative: when in doubt between OK and NEEDS_HELP choose NEEDS_HELP; between "
        "NEEDS_HELP and URGENT choose URGENT. Keep the summary free of diagnoses."
    )


def build_classifier(ctx: RunContext) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=make_model(ctx, temperature=0.0, max_tokens=500),
        system_prompt=system_prompt(ctx),
        structured_output_model=Classification,
        hooks=[ModelCallGuard(max_calls=4)],
        callback_handler=None,
    )


def transcript_text(attempt: CheckinAttempt) -> str:
    lines = [f"{t.speaker.upper()}: {t.text}" for t in attempt.transcript]
    answers = "\n".join(f"- {k}: {v}" for k, v in attempt.answers.items()) or "- none recorded"
    flags = "\n".join(f"- {f}" for f in attempt.urgent_flags) or "- none"
    return (
        "Transcript:\n"
        + "\n".join(lines)
        + "\n\nRecorded answers:\n"
        + answers
        + "\n\nAgent flagged urgent mid-call:\n"
        + flags
    )


def _validate_vocab(ctx: RunContext, resident: Resident, raw: Classification) -> CheckinResult:
    profile = ctx.profile
    needs = [n for n in raw.needs if n in profile.need_ids()]
    flags = [f for f in raw.red_flags if f in profile.red_flag_categories()]
    dropped = [x for x in raw.needs + raw.red_flags if x not in needs + flags]
    if dropped:
        ctx.audit.record(
            actor=f"agent:{AGENT_NAME}",
            type="note",
            resident_id=resident.id,
            reason=f"dropped labels outside the profile vocabulary: {dropped}",
        )
    if raw.red_flags and not flags:
        flags = ["other"] if "other" in profile.red_flag_categories() else []
    return CheckinResult(
        status=CheckinStatus(raw.status),
        needs=needs,
        red_flags=flags,
        indoor_temp_hint=raw.indoor_temp_hint,
        language=resident.language,
        confidence=raw.confidence,
        key_quote=raw.key_quote[:200],
        summary=raw.summary[:300],
    )


async def classify_attempt(
    ctx: RunContext, resident: Resident, attempt: CheckinAttempt
) -> CheckinResult:
    actor = f"agent:{AGENT_NAME}"
    if not attempt.answered:
        draft = CheckinResult(
            status=CheckinStatus.NO_ANSWER, language=resident.language, summary="No answer."
        )
    else:
        try:
            agent = build_classifier(ctx)
            result = await asyncio.wait_for(
                agent.invoke_async(
                    transcript_text(attempt),
                    invocation_state=ctx.invocation_state(resident_id=resident.id, actor=actor),
                ),
                ctx.settings.step_timeout_seconds,
            )
            raw = result.structured_output
            if not isinstance(raw, Classification):
                raise ValueError("no structured output")
            draft = _validate_vocab(ctx, resident, raw)
        except Exception as exc:  # noqa: BLE001 - the backstop must still run
            ctx.audit.record(
                actor=actor,
                type="note",
                resident_id=resident.id,
                reason=f"classifier failed, treating as UNCLEAR: {type(exc).__name__}: {exc}",
            )
            draft = CheckinResult(
                status=CheckinStatus.UNCLEAR,
                language=resident.language,
                summary="Classifier failed; result unclear.",
            )

    if attempt.urgent_flags and draft.status != CheckinStatus.URGENT:
        ctx.audit.record(
            actor="system:mid-call-flag",
            type="backstop",
            resident_id=resident.id,
            reason=(
                "check-in agent flagged urgent mid-call but the classifier said "
                f"{draft.status}; raised to URGENT"
            ),
            data={"flags": attempt.urgent_flags},
        )
        draft.status = CheckinStatus.URGENT
        if not draft.red_flags:
            draft.red_flags = ["other"]
    draft.flagged_mid_call = bool(attempt.urgent_flags)

    final = apply_backstop(draft, attempt.resident_text(), ctx.profile)
    ctx.audit.record(
        actor=actor,
        type="classification",
        resident_id=resident.id,
        reason=f"{final.status}; needs={final.needs}; red_flags={final.red_flags}",
        rationale=final.summary,
        data={"confidence": final.confidence, "key_quote": final.key_quote},
    )
    bs = final.backstop
    if bs and bs.disagreement != "none":
        if bs.disagreement == "backstop_raised":
            reason = (
                f"backstop raised {bs.model_status} to URGENT: matched "
                f"{bs.matched_categories} via {bs.matched_phrases}"
            )
        else:
            reason = "model said URGENT but no red-flag phrase matched; kept URGENT (never lowered)"
        ctx.audit.record(
            actor="system:backstop",
            type="backstop",
            resident_id=resident.id,
            reason=reason,
            data=bs.model_dump(),
        )
    return final
