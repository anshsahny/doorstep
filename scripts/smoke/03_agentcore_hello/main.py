"""Smoke test 03: a minimal Strands Agents agent on Amazon Bedrock AgentCore Runtime (Nova 2 Lite).

`run.sh` copies this file over the `main.py` that `agentcore create` generated in
`DoorstepHello/app/hello/`, then deploys and invokes it twice with one runtime session ID.
One Agent is kept per session ID, so the second invocation can recall what the first one said.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

MODEL_ID = os.getenv("DOORSTEP_MODEL_AGENT", "us.amazon.nova-2-lite-v1:0")
SYSTEM_PROMPT = "You are a smoke-test agent for Doorstep. Answer in one short sentence."
MAX_SESSIONS = 32

app = BedrockAgentCoreApp()
_agents: OrderedDict[str, Agent] = OrderedDict()


def agent_for(session_id: str) -> Agent:
    """Return the Agent bound to this runtime session, creating it on first use (LRU-bounded)."""
    if session_id in _agents:
        _agents.move_to_end(session_id)
        return _agents[session_id]
    if len(_agents) >= MAX_SESSIONS:
        _agents.popitem(last=False)
    _agents[session_id] = Agent(
        model=BedrockModel(model_id=MODEL_ID),
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )
    return _agents[session_id]


@app.entrypoint
def invoke(payload: Any, context: Any) -> dict[str, Any]:
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "payload.prompt must be a non-empty string"}
    session_id = getattr(context, "session_id", None) or "default-session"
    agent = agent_for(session_id)
    result = agent(prompt)
    return {
        "result": str(result).strip(),
        "session_id": session_id,
        "model_id": MODEL_ID,
        "messages_in_session": len(agent.messages),
    }


if __name__ == "__main__":
    app.run()
