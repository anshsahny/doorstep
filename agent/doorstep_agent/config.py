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

load_dotenv(ROOT / ".env")
os.environ.setdefault("AWS_PROFILE", "doorstep")
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
    checkin_max_turns: int = 6
    # Hard ceiling on any single model-backed step (one agent turn, one persona reply, one
    # classification, one dispatch). A step that overruns is treated as failed, never retried
    # forever, so a drill always finishes.
    step_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Return the cached settings."""
    return Settings(
        aws_profile=os.environ["AWS_PROFILE"],
        aws_region=os.environ["AWS_REGION"],
        model_agent=os.getenv("DOORSTEP_MODEL_AGENT", "us.amazon.nova-2-lite-v1:0"),
        model_persona=os.getenv("DOORSTEP_MODEL_PERSONA", "us.amazon.nova-micro-v1:0"),
        data_dir=ROOT / "data",
        policies_dir=ROOT / "agent" / "policies",
        default_profile=os.getenv("DOORSTEP_PROFILE", "heat"),
        default_alert_fixture=Path(
            os.getenv(
                "DOORSTEP_ALERT_FIXTURE",
                str(ROOT / "data" / "alerts" / "2021-06-pqr-excessive-heat-warning.json"),
            )
        ),
    )
