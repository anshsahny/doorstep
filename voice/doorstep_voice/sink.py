"""Where a voice call's events go, and the loading of what a call needs to start.

`CoordinatorSink` sends `checkin_urgent` and `checkin_attempt` to the incident's coordinator on
AgentCore Runtime with an IAM-signed `InvokeAgentRuntime` call, on the incident's own runtime
session, exactly as the Lambdas do. Nothing a caller sends reaches it: the incident, resident and
attempt come from the verified token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import boto3
from botocore.config import Config

from doorstep_agent.cloud.coordinator import runtime_session_id
from doorstep_agent.models import CaseState
from doorstep_agent.profiles import load_profile
from doorstep_agent.store import NotFound
from doorstep_agent.store_dynamo import DynamoBackend

from .session import CallSetup

log = logging.getLogger(__name__)

CALLABLE_STATES = frozenset({CaseState.QUEUED, CaseState.NO_ANSWER, CaseState.UNCLEAR})

# The coordinator answers in about a second (its work runs in the background). botocore's default
# 60 s read timeout let a dead pooled connection hold a finished call's result for 61.7 s in the
# phone bridge after it had idled for hours (Phase 4, call 2). A page must never wait like that:
# short timeouts, TCP keepalive, and retries (every event is claimed once, so a retry is safe).
COORDINATOR_CLIENT_CONFIG = Config(
    connect_timeout=5,
    read_timeout=10,
    retries={"max_attempts": 4, "mode": "standard"},
    tcp_keepalive=True,
)


def coordinator_client(session: boto3.Session | None = None):  # noqa: ANN201
    return (session or boto3).client("bedrock-agentcore", config=COORDINATOR_CLIENT_CONFIG)


class SetupError(ValueError):
    """The token is valid but the call cannot happen (no such case, already done). Safe text."""


def load_setup(
    backend: DynamoBackend,
    claims: dict[str, Any],
    *,
    channel: str,
    input_rate: int,
    output_rate: int,
    max_seconds: float,
) -> CallSetup:
    incident_id, resident_id = claims["inc"], claims["res"]
    try:
        incident = backend.for_incident(incident_id).incident(incident_id)
    except NotFound as exc:
        raise SetupError("no such incident") from exc
    if resident_id not in incident.resident_ids:
        raise SetupError("resident is not in this incident")
    if incident.mode != claims["mode"]:
        raise SetupError("token mode does not match the incident")
    store = backend.for_incident(incident_id, incident.resident_ids)
    try:
        case = store.case(incident_id, resident_id)
        resident = store.resident(resident_id)
    except NotFound as exc:
        raise SetupError("no case for this resident") from exc
    if case.state not in CALLABLE_STATES:
        raise SetupError(f"this resident's case is {case.state}, not waiting for a call")
    return CallSetup(
        claims=claims,
        resident=resident,
        profile=load_profile(incident.profile_id),
        org=store.org(),
        channel=channel,
        input_rate=input_rate,
        output_rate=output_rate,
        max_seconds=max_seconds,
    )


class CoordinatorSink:
    def __init__(
        self,
        *,
        runtime_arn: str,
        backend: DynamoBackend,
        client: Any = None,
    ) -> None:
        self.runtime_arn = runtime_arn
        self.backend = backend
        self.client = client or coordinator_client()

    def _invoke(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        result = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=runtime_session_id(incident_id),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"incident_id": incident_id, "event": event}).encode(),
        )
        body = result["response"]
        raw = body.read() if hasattr(body, "read") else body
        answer = json.loads(raw or b"{}")
        log.info(
            "coordinator %s for %s: %s in %.2fs",
            event.get("type"),
            event.get("resident_id"),
            {k: answer.get(k) for k in ("ok", "accepted", "reason", "error")},
            time.monotonic() - started,
        )
        return answer

    async def urgent(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        await asyncio.to_thread(self._invoke, claims["inc"], {"type": "checkin_urgent", **event})

    async def attempt(self, claims: dict[str, Any], event: dict[str, Any]) -> None:
        await asyncio.to_thread(self._invoke, claims["inc"], {"type": "checkin_attempt", **event})

    async def page_delivered(self, claims: dict[str, Any]) -> bool:
        return await asyncio.to_thread(page_is_out, self.backend, claims["inc"], claims["res"])


def page_is_out(backend: DynamoBackend, incident_id: str, resident_id: str) -> bool:
    """A decision about this resident has been put in front of a human.

    With Telegram on, that means a recorded delivery. Without it (sandbox, drills) the board is
    the channel, so a pending decision is already in front of the captain.
    """
    store = backend.for_incident(incident_id)
    incident = store.incident(incident_id)
    telegram = bool(incident.run_options.get("telegram", False))
    for decision in store.decisions(incident_id):
        if decision.resident_id != resident_id or decision.status == "draft":
            continue
        if not telegram or decision.delivery:
            return True
    return False
