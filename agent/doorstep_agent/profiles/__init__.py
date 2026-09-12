"""Hazard profiles (SPEC §3a): one YAML file per hazard, merged over `_shared.yaml`."""

from .loader import (
    PROFILES_DIR,
    AlertEvent,
    CheckinQuestion,
    HazardProfile,
    Need,
    RedFlagRule,
    RiskFactor,
    available_profiles,
    load_profile,
)

__all__ = [
    "PROFILES_DIR",
    "AlertEvent",
    "CheckinQuestion",
    "HazardProfile",
    "Need",
    "RedFlagRule",
    "RiskFactor",
    "available_profiles",
    "load_profile",
]
