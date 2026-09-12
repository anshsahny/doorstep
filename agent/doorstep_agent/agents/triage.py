"""triage: turns deterministic risk scores plus memory notes into call waves (SPEC §6, F2).

A Strands Agent with tools and structured output. It may move a resident up one wave with a
reason; `risk.validate_call_plan` enforces that nobody moves down, is dropped or duplicated.
"""

from __future__ import annotations

from strands import Agent

from ..audit import AuditHook
from ..guards import ModelCallGuard
from ..models import CallPlan
from ..runtime import RunContext
from ..tools import get_resident_memory, get_roster, score_residents
from ._common import make_model

AGENT_NAME = "triage"


def system_prompt(ctx: RunContext) -> str:
    t = ctx.profile.wave_thresholds
    return (
        f"You plan the call order for {ctx.org.name}'s check-ins during a "
        f"'{ctx.profile.display_name}' incident. Built with Strands Agents.\n\n"
        "Steps:\n"
        "1. Call get_roster, then score_residents. The scores and waves are deterministic and "
        f"come from the hazard profile: wave 1 is a score of {t.wave1_min} or more, wave 2 is "
        f"{t.wave2_min} to {t.wave1_min - 1}, wave 3 is below {t.wave2_min}.\n"
        "2. The roster already carries each resident's notes (what the team remembers). If a "
        "wave 2 or 3 resident's note hints at extra risk you may move them UP by exactly one "
        "wave, and you must give the reason in `adjustments`. Never move anyone down. Do not "
        "move anyone without a concrete reason from the notes. Call get_resident_memory only "
        "when a note is unclear, for at most two residents.\n"
        "3. Return the CallPlan: waves 1, 2 and 3 with every resident id exactly once, highest "
        "score first inside each wave, plus one or two sentences of notes for the captain.\n"
        "Use resident ids exactly as given. Do not invent residents."
    )


def build_triage_agent(ctx: RunContext) -> Agent:
    return Agent(
        name=AGENT_NAME,
        model=make_model(ctx, temperature=0.1, max_tokens=1200),
        system_prompt=system_prompt(ctx),
        tools=[get_roster, score_residents, get_resident_memory],
        structured_output_model=CallPlan,
        hooks=[AuditHook(f"agent:{AGENT_NAME}"), ModelCallGuard(max_calls=30)],
        callback_handler=None,
    )
