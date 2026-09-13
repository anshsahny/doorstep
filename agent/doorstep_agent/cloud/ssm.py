"""Secrets and switches from SSM Parameter Store, read at runtime and never logged.

Deployed code gets no secret through its environment configuration, its CloudFormation template
or its outputs. At start-up the process reads the SecureString parameters it is allowed to read
and places them in the environment variable names the Phase 1–2 code already resolves
(`resolve_ref("env:TELEGRAM_CAPTAIN_CHAT_ID")`, `CALL_ALLOWLIST`), so nothing downstream changes.
The values live in this process's memory only. Functions here return names, never values.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3

log = logging.getLogger(__name__)

PREFIX = os.getenv("DOORSTEP_SSM_PREFIX", "/doorstep")

# env var the existing code reads -> parameter under PREFIX
RUNTIME_SECRETS: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "telegram/bot_token",
    "TELEGRAM_CAPTAIN_CHAT_ID": "telegram/captain_chat_id",
    "TELEGRAM_VOLUNTEER_CHAT_IDS": "telegram/volunteer_chat_ids",
    "CALL_ALLOWLIST": "call_allowlist",
    "OPERATOR_TEST_NUMBER": "operator_test_number",
}


def hydrate_env(
    mapping: dict[str, str] = RUNTIME_SECRETS, *, client: Any = None, prefix: str = PREFIX
) -> list[str]:
    """Load parameters into the environment. Returns the env names that had no parameter."""
    client = client or boto3.client("ssm")
    names = {f"{prefix}/{param}": env for env, param in mapping.items()}
    found: dict[str, str] = {}
    keys = list(names)
    for start in range(0, len(keys), 10):  # GetParameters takes at most 10 names
        response = client.get_parameters(Names=keys[start : start + 10], WithDecryption=True)
        found.update({p["Name"]: p["Value"] for p in response.get("Parameters", [])})
    missing = []
    for name, env in names.items():
        if found.get(name):
            os.environ[env] = found[name]
        else:
            missing.append(env)
    if missing:
        log.warning("SSM parameters missing for: %s", ", ".join(sorted(missing)))
    return missing


class SsmFlags:
    """Operational switches, re-read at most every `ttl` seconds.

    The kill switch fails closed: if it has never been readable, spending is refused. Once read,
    a transient SSM error keeps the last value rather than flapping.
    """

    def __init__(self, *, client: Any = None, prefix: str = PREFIX, ttl: float = 30.0) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl = ttl
        self._cache: dict[str, tuple[float, str | None]] = {}

    def _value(self, param: str) -> str | None:
        now = time.monotonic()
        cached = self._cache.get(param)
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        try:
            client = self._client or boto3.client("ssm")
            value = client.get_parameter(Name=f"{self._prefix}/{param}")["Parameter"]["Value"]
        except Exception as exc:  # noqa: BLE001 - reported below, never with a value
            log.warning("could not read %s/%s: %s", self._prefix, param, type(exc).__name__)
            value = cached[1] if cached else None
        self._cache[param] = (now, value)
        return value

    def kill_switch(self) -> bool:
        value = self._value("kill_switch")
        return value is None or value.strip().lower() not in ("off", "false", "0")
