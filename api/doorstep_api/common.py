"""Shared plumbing for the Lambda handlers. No secret value is ever logged or returned."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import ClientError

PREFIX = os.getenv("DOORSTEP_SSM_PREFIX", "/doorstep")
INCIDENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
CLAIM_TTL_SECONDS = 14 * 24 * 3600


def conditional_failed(exc: ClientError) -> bool:
    """Keep in step with `doorstep_agent.store_dynamo.conditional_failed`: match the error code,
    never the lazily built (and not thread-safe) `client.exceptions` class."""
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def runtime_session_id(incident_id: str) -> str:
    """Keep in step with `doorstep_agent.cloud.coordinator.runtime_session_id` (tested)."""
    base = f"doorstep-incident-{incident_id}"
    if len(base) < 33:
        base += "-" + hashlib.sha256(incident_id.encode()).hexdigest()[:16]
    return base[:100]


def normalize_number(raw: str | None) -> str | None:
    """Keep in step with `doorstep_agent.runtime.normalize_number` (a test compares the two)."""
    if not raw:
        return None
    cleaned = re.sub(r"[\s().-]", "", raw)
    return cleaned if _E164.match(cleaned) else None


def log(**fields: Any) -> None:
    """One JSON line to CloudWatch. Callers pass names and outcomes, never secrets."""
    print(json.dumps(fields, default=str))


def response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def body_of(event: dict[str, Any]) -> dict[str, Any] | None:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def header(event: dict[str, Any], name: str) -> str:
    return str((event.get("headers") or {}).get(name.lower(), ""))


def same_secret(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), str(expected).encode())


@dataclass
class Deps:
    """AWS clients, created lazily so tests can swap any of them."""

    table: str = field(default_factory=lambda: os.getenv("DOORSTEP_TABLE", ""))
    runtime_arn: str = field(default_factory=lambda: os.getenv("DOORSTEP_RUNTIME_ARN", ""))
    voice_runtime_arn: str = field(
        default_factory=lambda: os.getenv("DOORSTEP_VOICE_RUNTIME_ARN", "")
    )
    _ssm: Any = None
    _dynamodb: Any = None
    _agentcore: Any = None
    _params: dict[str, tuple[float, str | None]] = field(default_factory=dict)

    _session: Any = None

    @property
    def boto_session(self) -> Any:
        self._session = self._session or boto3.Session()
        return self._session

    @property
    def ssm(self) -> Any:
        self._ssm = self._ssm or boto3.client("ssm")
        return self._ssm

    @property
    def dynamodb(self) -> Any:
        self._dynamodb = self._dynamodb or boto3.client("dynamodb")
        return self._dynamodb

    @property
    def agentcore(self) -> Any:
        self._agentcore = self._agentcore or boto3.client("bedrock-agentcore")
        return self._agentcore

    # --- SSM ---

    def param(self, name: str, *, ttl: float = 60.0) -> str | None:
        now = time.monotonic()
        cached = self._params.get(name)
        if cached and now - cached[0] < ttl:
            return cached[1]
        try:
            value = self.ssm.get_parameter(Name=f"{PREFIX}/{name}", WithDecryption=True)[
                "Parameter"
            ]["Value"]
        except Exception as exc:  # noqa: BLE001 - the name is logged, the value never
            log(level="warning", msg="ssm read failed", param=name, error=type(exc).__name__)
            value = cached[1] if cached else None
        self._params[name] = (now, value)
        return value

    def kill_switch(self) -> bool:
        """Fails closed: a switch that cannot be read refuses spending."""
        value = self.param("kill_switch", ttl=15.0)
        return value is None or value.strip().lower() not in ("off", "false", "0")

    # --- DynamoDB ---

    def claim(self, key: str, **attrs: str) -> bool:
        item = {
            "PK": {"S": f"CLAIM#{key}"},
            "SK": {"S": "CLAIM"},
            "ttl": {"N": str(int(time.time()) + CLAIM_TTL_SECONDS)},
            **{k: {"S": v} for k, v in attrs.items()},
        }
        try:
            self.dynamodb.put_item(
                TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(PK)"
            )
            return True
        except ClientError as exc:
            if not conditional_failed(exc):
                raise
            return False

    def claimed(self, key: str) -> dict[str, str]:
        item = self.dynamodb.get_item(
            TableName=self.table,
            Key={"PK": {"S": f"CLAIM#{key}"}, "SK": {"S": "CLAIM"}},
            ConsistentRead=True,
        ).get("Item", {})
        return {k: v["S"] for k, v in item.items() if "S" in v}

    def release(self, key: str) -> None:
        self.dynamodb.delete_item(
            TableName=self.table, Key={"PK": {"S": f"CLAIM#{key}"}, "SK": {"S": "CLAIM"}}
        )

    def count(self, key: str, *, limit: int, ttl_seconds: int) -> bool:
        """Add one to a counter unless it has reached `limit`. True if counted."""
        try:
            self.dynamodb.update_item(
                TableName=self.table,
                Key={"PK": {"S": key}, "SK": {"S": "COUNT"}},
                UpdateExpression="ADD n :one SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="attribute_not_exists(n) OR n < :limit",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":limit": {"N": str(limit)},
                    ":ttl": {"N": str(int(time.time()) + ttl_seconds)},
                },
            )
            return True
        except ClientError as exc:
            if not conditional_failed(exc):
                raise
            return False

    def reached(self, key: str, limit: int) -> bool:
        """True if a counter is already at `limit` (a read; nothing is counted)."""
        item = self.dynamodb.get_item(
            TableName=self.table,
            Key={"PK": {"S": key}, "SK": {"S": "COUNT"}},
            ConsistentRead=True,
        ).get("Item")
        return bool(item) and int(item.get("n", {}).get("N", "0")) >= limit

    def uncount(self, key: str) -> None:
        """Take back one count (a later limit refused the request, or nothing ran)."""
        try:
            self.dynamodb.update_item(
                TableName=self.table,
                Key={"PK": {"S": key}, "SK": {"S": "COUNT"}},
                UpdateExpression="ADD n :minus",
                ConditionExpression="n > :zero",
                ExpressionAttributeValues={":minus": {"N": "-1"}, ":zero": {"N": "0"}},
            )
        except ClientError as exc:
            if not conditional_failed(exc):
                raise
            pass

    # --- the coordinator ---

    def invoke(self, incident_id: str, event: dict[str, Any]) -> dict[str, Any]:
        result = self.agentcore.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=runtime_session_id(incident_id),
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({"incident_id": incident_id, "event": event}).encode(),
        )
        raw = (
            result["response"].read() if hasattr(result["response"], "read") else result["response"]
        )
        return json.loads(raw or b"{}")


DEPS = Deps()
