"""persona: a simulated resident, driven by the Strands Evals `ActorSimulator` on Nova Micro.

Personas live in `evals/personas/<profile>/*.yaml` with hidden ground truth. A persona with
`behaviour: no_answer` never picks up. Everything else is played by the simulator: the check-in
agent speaks, the simulator answers in character, until the agent ends the call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from strands_evals.simulation import ActorSimulator
from strands_evals.types.simulation import ActorProfile

from ..models import Language


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "NEEDS_HELP", "URGENT", "NO_ANSWER"]
    red_flags: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    hidden: bool = False


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    resident_id: str
    display_name: str
    language: Language = "en"
    behaviour: Literal["answers", "no_answer"] = "answers"
    traits: dict[str, str | int | bool] = Field(default_factory=dict)
    context: str = ""
    goal: str = ""
    opening: str = ""
    max_turns: int = 6
    ground_truth: GroundTruth
    fictional: bool = True


def load_personas(directory: Path) -> list[Persona]:
    if not directory.is_dir():
        raise FileNotFoundError(f"persona directory not found: {directory}")
    personas = [
        Persona.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(directory.glob("*.yaml"))
    ]
    ids = [p.id for p in personas]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate persona ids in {directory}")
    return personas


class ResidentReply(BaseModel):
    """The simulator's structured turn (must carry `message` and `stop`)."""

    reasoning: str = Field("", description="One short clause on why the resident answers so")
    stop: bool = Field(
        False,
        description="True ONLY after the caller has said goodbye, or if the resident hangs up",
    )
    message: str | None = Field(None, description="The resident's spoken reply, 1-2 sentences")


PERSONA_TEMPLATE = """You are role-playing one real person who has just answered their home phone.
Stay in character for the whole call. Speak as this person would on the phone: short, natural,
spoken sentences, 1-2 per turn. Never mention that you are simulated, an AI, or a test.
You are the one being checked on: never give advice, never ask the caller questions about
themselves, never solve anything.

Who you are:
{actor_profile}

Rules:
- Answer only what the caller asks. Reveal your situation the way this person would: do not
  volunteer more than the profile says, but answer honestly when asked directly.
- Keep speaking with the caller until they say goodbye. stop must be false until then.
- Set stop=true only when the caller has said goodbye or ended the call, or if the profile says
  you hang up. Even then, message can hold your last words.
"""


class NoAnswerChannel:
    """A resident who never picks up."""

    async def answer(self) -> str | None:
        return None

    async def reply(self, agent_text: str) -> str | None:
        return None


class PersonaChannel:
    """A resident played by the Strands Evals ActorSimulator."""

    def __init__(self, persona: Persona, model_id: str) -> None:
        self.persona = persona
        self.model_id = model_id
        self._sim: ActorSimulator | None = None

    def _opening(self) -> str:
        if self.persona.opening:
            return self.persona.opening
        return "¿Bueno?" if self.persona.language == "es" else "Hello?"

    def _build(self) -> ActorSimulator:
        p = self.persona
        profile = ActorProfile(
            traits={"first_name": p.display_name.split()[0], "language": p.language, **p.traits},
            context=p.context,
            actor_goal=p.goal,
        )
        return ActorSimulator(
            actor_profile=profile,
            initial_query=self._opening(),
            system_prompt_template=PERSONA_TEMPLATE,
            model=self.model_id,
            max_turns=p.max_turns,
            structured_output_model=ResidentReply,
        )

    async def answer(self) -> str | None:
        if self.persona.behaviour == "no_answer":
            return None
        self._sim = self._build()
        return self._opening()

    async def reply(self, agent_text: str) -> str | None:
        if self._sim is None:
            return None
        if not self._sim.has_next():
            return None
        result = await asyncio.to_thread(self._sim.act, agent_text)
        reply = result.structured_output
        message = (reply.message or "").strip() if isinstance(reply, ResidentReply) else ""
        if not message:
            return None
        return message
