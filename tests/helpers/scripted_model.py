"""A deterministic Strands `Model` double, so interrupt tests run offline.

The Strands Agents SDK ships no mock provider, so tests that need the real agent loop, tool
executor, hooks and interrupt machinery — but not a real model — drive it with this one. It
replays a fixed script of turns: each turn is either tool calls or a final text answer.

Everything except the model provider is the real SDK, which is the point: the interrupt and
resume behaviour under test is the SDK's, not a simulation of it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from typing import Any

from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec


@dataclass
class ToolCall:
    """One tool use the scripted model should emit."""

    name: str
    input: dict[str, Any]
    tool_use_id: str


@dataclass
class Turn:
    """One model turn: either tool calls, or a final text answer."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""


class ScriptedModel(Model):
    """Replays `turns`, choosing the next one from the conversation, not from a call counter.

    The turn is picked by counting the tool-result rounds already in `messages`, so a model
    rebuilt in a second process resumes where the first one left off instead of replaying its
    first turn. That is what a real model does — it sees the history — and without it a restart
    test would call the same tool twice and blame the SDK for it (Spike B, 2026-09-12).

    `model_calls` counts the calls this instance actually served.
    """

    def __init__(self, turns: list[Turn], *, final_text: str = "done") -> None:
        self.turns = turns
        self.final_text = final_text
        self.model_calls = 0
        self._config: dict[str, Any] = {"model_id": "scripted"}

    @staticmethod
    def _completed_rounds(messages: Messages) -> int:
        """How many turns this conversation has already answered: one per tool-result message."""
        return sum(
            1
            for message in messages
            if any("toolResult" in block for block in message.get("content", []))
        )

    # --- Model interface ---

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("ScriptedModel does not support structured output")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.model_calls += 1
        index = self._completed_rounds(messages)
        turn = self.turns[index] if index < len(self.turns) else Turn(text=self.final_text)

        yield {"messageStart": {"role": "assistant"}}
        if turn.tool_calls:
            for block, call in enumerate(turn.tool_calls):
                yield {
                    "contentBlockStart": {
                        "contentBlockIndex": block,
                        "start": {"toolUse": {"name": call.name, "toolUseId": call.tool_use_id}},
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": block,
                        "delta": {"toolUse": {"input": json.dumps(call.input)}},
                    }
                }
                yield {"contentBlockStop": {"contentBlockIndex": block}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": turn.text}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }
