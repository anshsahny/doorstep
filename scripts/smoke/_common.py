"""Shared helpers for the Phase 0 smoke scripts.

Loads `.env` from the repo root, applies the AWS profile/region defaults from SPEC §15,
and provides small logging helpers with one exit contract:
exit 0 = PASS, exit 1 = FAIL, exit 2 = missing or invalid configuration.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

os.environ.setdefault("AWS_PROFILE", "doorstep")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", os.environ["AWS_REGION"])

AWS_PROFILE = os.environ["AWS_PROFILE"]
REGION = os.environ["AWS_REGION"]
MODEL_AGENT = os.getenv("DOORSTEP_MODEL_AGENT", "us.amazon.nova-2-lite-v1:0")
MODEL_PERSONA = os.getenv("DOORSTEP_MODEL_PERSONA", "us.amazon.nova-micro-v1:0")
MODEL_VOICE = os.getenv("DOORSTEP_MODEL_VOICE", "amazon.nova-2-sonic-v1:0")

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_T0 = time.monotonic()


def _stamp() -> str:
    return f"[{time.monotonic() - _T0:6.1f}s]"


def step(msg: str) -> None:
    print(f"{_stamp()} ... {msg}", flush=True)


def info(msg: str) -> None:
    print(f"{_stamp()}     {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{_stamp()} OK  {msg}", flush=True)


def passed(msg: str) -> None:
    print(f"\n{_stamp()} PASS: {msg}", flush=True)
    sys.exit(0)


def fail(msg: str, code: int = 1) -> None:
    print(f"\n{_stamp()} FAIL: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def env(name: str, *, required: bool = True, default: str | None = None) -> str:
    """Return an environment variable, exiting with code 2 if a required one is missing."""
    value = os.getenv(name, default)
    if required and not value:
        fail(f"{name} is not set. Copy .env.example to .env and fill it in.", code=2)
    return value or ""


def e164(name: str) -> str:
    """Return a phone number from the environment, validated as E.164."""
    value = env(name).strip()
    if not _E164.match(value):
        fail(f"{name}={value!r} is not E.164 (example: +15035551234)", code=2)
    return value


def redact(value: str, keep: int = 4) -> str:
    """Show only the first and last few characters of an identifier."""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"
