"""A deterministic action through the real Strands Agents tool loop (Cedar, hooks, audit).

Some actions are decided by code, not by a model: outreach placing a live check-in call is one
(SPEC §6: the outreach step is deterministic). They must still cross the same boundary as a
model's tool call, so Cedar decides and the audit hook records them. A direct `agent.tool.x()`
call cannot do that here: Strands merges the invocation state into the tool input, which cannot
carry the RunContext (Phase 4 spike S4). So the action is proposed by this one-turn model and
executed by an ordinary `Agent` with the ordinary interventions and hooks.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from typing import Any

from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec


class PlannedAction(Model):
    """Proposes exactly one tool call, then ends the turn with a fixed sentence."""

    def __init__(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        self._config: dict[str, Any] = {"model_id": "planned-action"}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("a planned action has no structured output")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        done = any(
            "toolResult" in block for message in messages for block in message.get("content", [])
        )
        yield {"messageStart": {"role": "assistant"}}
        if not done:
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"name": self.tool_name, "toolUseId": self.tool_use_id}},
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": json.dumps(self.tool_input)}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "done"}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }
