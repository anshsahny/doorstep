"""AgentCore Runtime entrypoint for Doorstep voice (Strands Agents BidiAgent on Nova 2 Sonic).

The same image as the coordinator, a different runtime (`doorstep_voice`) and a narrower role: it
can stream one model, read the incident it is calling for, claim a token, and send events to the
coordinator. It holds no Telegram or Twilio secret.

A browser reaches `/ws` with a 60-second SigV4 presigned URL minted by `POST /voice/session`; the
voice token rides in that URL as a signed custom header parameter, so it cannot be swapped without
breaking the signature.
"""

from __future__ import annotations

import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from doorstep_agent.cloud.logs import configure_logging
from doorstep_agent.cloud.ssm import SsmFlags, hydrate_env
from doorstep_agent.config import settings
from doorstep_agent.store_dynamo import DynamoBackend

from .serve import VoiceDeps, serve_browser, token_from
from .sink import CoordinatorSink

app = BedrockAgentCoreApp()
_deps: VoiceDeps | None = None


def build_deps() -> VoiceDeps:
    configure_logging()
    hydrate_env({"INTERNAL_HMAC_SECRET": "internal_hmac_secret"})
    cfg = settings()
    # No identity map: this process polls rows the coordinator writes.
    backend = DynamoBackend(cfg.table_name, cfg.org_id, cache=False)
    return VoiceDeps(
        backend=backend,
        sink=CoordinatorSink(runtime_arn=os.environ["DOORSTEP_COORDINATOR_ARN"], backend=backend),
        flags=SsmFlags(),
    )


def deps() -> VoiceDeps:
    global _deps
    if _deps is None:
        _deps = build_deps()
    return _deps


@app.websocket
async def voice(websocket: Any, context: Any) -> None:
    await websocket.accept()
    headers = getattr(context, "request_headers", None) or {}
    token = token_from(headers, dict(websocket.query_params))
    await serve_browser(websocket, token, deps())


@app.entrypoint
async def invoke(payload: Any, context: Any) -> dict[str, Any]:
    """Health only: calls come in over `/ws`."""
    return {"ok": True, "service": "doorstep-voice"}


if __name__ == "__main__":
    app.run()
