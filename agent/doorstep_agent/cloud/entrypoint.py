"""AgentCore Runtime entrypoint for the Doorstep coordinator (Strands Agents on Bedrock AgentCore).

The container runs `opentelemetry-instrument python -m doorstep_agent.cloud.entrypoint`, which
serves the runtime contract: `POST /invocations` on port 8080 and `GET /ping`. Spike S1 proved
the pieces this relies on locally: an async entrypoint returns while its background work keeps
`/ping` at `HealthyBusy`, and a second invocation is served while that work runs.

Configuration is the environment the CDK stack sets (table, bucket, org — names, no secrets) plus
the SSM parameters read once at start (`ssm.hydrate_env`).
"""

from __future__ import annotations

import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ..config import settings
from ..notify.telegram import Bot
from ..store_dynamo import DynamoBackend
from .coordinator import Coordinator
from .logs import configure_logging
from .ssm import SsmFlags, hydrate_env

app = BedrockAgentCoreApp()
_coordinator: Coordinator | None = None


class AppTracker:
    """Background tasks reported to the runtime, so `/ping` says `HealthyBusy` while they run."""

    def start(self, name: str) -> int:
        return app.add_async_task(name)

    def done(self, token: int) -> None:
        app.complete_async_task(token)


def build_coordinator() -> Coordinator:
    configure_logging()
    hydrate_env()
    cfg = settings()
    if not cfg.table_name:
        raise RuntimeError("DOORSTEP_TABLE is not set")
    return Coordinator(
        DynamoBackend(cfg.table_name, cfg.org_id),
        flags=SsmFlags(),
        cfg=cfg,
        tracker=AppTracker(),
        bot_factory=lambda: Bot(os.environ["TELEGRAM_BOT_TOKEN"]),
    )


@app.entrypoint
async def invoke(payload: Any, context: Any) -> dict[str, Any]:
    global _coordinator
    if _coordinator is None:
        _coordinator = build_coordinator()
    return await _coordinator.handle(payload if isinstance(payload, dict) else {})


if __name__ == "__main__":
    app.run()
