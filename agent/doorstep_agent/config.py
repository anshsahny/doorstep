"""Runtime settings for Doorstep.

Loads `.env` from the repo root, applies the AWS defaults from SPEC §15 and exposes one frozen
`Settings` object. Nothing hazard-specific lives here: hazards are profiles (SPEC §3a).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def running_in_aws() -> bool:
    """True inside Lambda or the AgentCore container, where credentials come from a role.

    There, forcing `AWS_PROFILE` would make boto3 look for a profile that does not exist, and a
    `.env` must never be the source of anything: deployed secrets come from SSM.
    """
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME")) or os.getenv("DOORSTEP_ENV") == "cloud"


def _profile_exists(name: str) -> bool:
    """Whether the shared AWS config or credentials file defines this profile."""
    import configparser

    files = {
        os.getenv("AWS_CONFIG_FILE", "~/.aws/config"): f"profile {name}",
        os.getenv("AWS_SHARED_CREDENTIALS_FILE", "~/.aws/credentials"): name,
    }
    for path, section in files.items():
        parser = configparser.ConfigParser()
        try:
            parser.read(Path(path).expanduser())
        except configparser.Error:
            continue
        if parser.has_section(section):
            return True
    return False


if not running_in_aws():
    load_dotenv(ROOT / ".env")
    # Only a machine that has the `doorstep` profile gets it by default. Forcing a profile that
    # does not exist (CI has none) makes every boto3 client fail, even ones given explicit keys.
    if "AWS_PROFILE" not in os.environ and _profile_exists("doorstep"):
        os.environ["AWS_PROFILE"] = "doorstep"
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", os.environ["AWS_REGION"])


@dataclass(frozen=True)
class Settings:
    """Process-wide configuration, read once from the environment."""

    aws_profile: str
    aws_region: str
    model_agent: str
    model_persona: str
    data_dir: Path
    policies_dir: Path
    # Where paused agent sessions are persisted locally; `sessions_bucket` moves them to S3.
    sessions_dir: Path
    sessions_bucket: str = ""
    # The DynamoDB table and organisation the cloud coordinator works on (empty locally).
    table_name: str = ""
    org_id: str = "juniper-court"
    # Which hazard profile this deployment runs, and the replay fixture for drills. This is
    # configuration: the agents, tools, policies and prompts never name a hazard themselves.
    default_profile: str = "heat"
    default_alert_fixture: Path = (
        ROOT / "data" / "alerts" / "2021-06-pqr-excessive-heat-warning.json"
    )
    # Check-in retries (SPEC §3 F3): attempts and the real-time spacing between them.
    max_attempts: int = 3
    retry_interval_minutes: int = 10
    # Drill mode compresses 10 minutes to 20 seconds (SPEC §6.3): factor 30.
    drill_time_compression: float = 30.0
    # How many simulated check-ins may run at once in a drill.
    drill_concurrency: int = 6
    # Longest text check-in, in agent turns.
    # Greeting + four questions + closing is six turns with no slack, so one repeated question
    # cut calls off before the last question (Phase 6 suite 1: protocol completion 54%).
    checkin_max_turns: int = 9
    # How long a human decision stays answerable before it expires (SPEC §4 is silent; a
    # decision nobody answers must fail safe rather than sit pending forever). This is **real**
    # minutes and is never compressed by drill mode: the captain reading it is a real person at
    # real speed. `make telegram-drill ARGS=--decision-ttl 0.5` shortens it to demo expiry.
    decision_ttl_minutes: float = 15.0
    # Hard ceiling on any single model-backed step (one agent turn, one persona reply, one
    # classification, one dispatch). A step that overruns is treated as failed, never retried
    # forever, so a drill always finishes.
    step_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Return the cached settings."""
    return Settings(
        aws_profile=os.getenv("AWS_PROFILE", ""),
        aws_region=os.environ["AWS_REGION"],
        model_agent=os.getenv("DOORSTEP_MODEL_AGENT", "us.amazon.nova-2-lite-v1:0"),
        model_persona=os.getenv("DOORSTEP_MODEL_PERSONA", "us.amazon.nova-micro-v1:0"),
        data_dir=ROOT / "data",
        policies_dir=ROOT / "agent" / "policies",
        sessions_dir=Path(os.getenv("DOORSTEP_SESSIONS_DIR", str(ROOT / ".sessions"))),
        sessions_bucket=os.getenv("DOORSTEP_SESSIONS_BUCKET", ""),
        table_name=os.getenv("DOORSTEP_TABLE", ""),
        org_id=os.getenv("DOORSTEP_ORG_ID", "juniper-court"),
        default_profile=os.getenv("DOORSTEP_PROFILE", "heat"),
        default_alert_fixture=Path(
            os.getenv(
                "DOORSTEP_ALERT_FIXTURE",
                str(ROOT / "data" / "alerts" / "2021-06-pqr-excessive-heat-warning.json"),
            )
        ),
    )
