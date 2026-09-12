"""Hazard profile loader (SPEC §3a).

A hazard profile is a YAML file. Everything that differs between hazards lives there: which NWS
events activate it, the risk weights, the check-in questions, the deterministic red-flag phrases,
the needs vocabulary, the relief-centre kind and the tips. The agents, tools, policies and the
drill runner read the active profile and contain nothing hazard-specific.

`_shared.yaml` in the same directory holds the parts common to every hazard (greeting, closing,
the "how are you feeling" question, the shared red-flag categories and needs). A profile is merged
on top of it unless it sets `include_shared: false`, in which case the profile must carry its own
`common` block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

PROFILES_DIR = Path(__file__).resolve().parent
SHARED_FILE = PROFILES_DIR / "_shared.yaml"

Language = Literal["en", "es"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlertEvent(_Strict):
    event: str
    vtec: str | None = None
    activation: Literal["auto", "ask"] = "auto"
    note: str = ""


class RiskFactor(_Strict):
    id: str
    label: str
    field: str
    equals: bool | int | str
    points: int = Field(ge=0)


class WaveThresholds(_Strict):
    wave1_min: int = 8
    wave2_min: int = 5


class CheckinQuestion(_Strict):
    id: str
    en: str
    es: str
    maps_to_needs: list[str] = Field(default_factory=list)

    def text(self, language: Language) -> str:
        return self.es if language == "es" else self.en


class RedFlagRule(_Strict):
    category: str
    label: str
    en: list[str] = Field(default_factory=list)
    es: list[str] = Field(default_factory=list)

    def phrases(self) -> list[str]:
        return [*self.en, *self.es]


class Need(_Strict):
    id: str
    label: str


class CommonScript(_Strict):
    greeting_en: str
    greeting_es: str
    intro_en: str
    intro_es: str
    closing_en: str
    closing_es: str
    red_flag_response_en: str
    red_flag_response_es: str
    emergency_response_en: str
    emergency_response_es: str
    voicemail_en: str
    voicemail_es: str


class ProfileScript(_Strict):
    hazard_phrase_en: str
    hazard_phrase_es: str
    hazard_short_en: str
    hazard_short_es: str
    tip_en: str
    tip_es: str


class HazardProfile(_Strict):
    """A fully merged hazard profile."""

    id: str
    display_name: str
    include_shared: bool = True
    alert_events: list[AlertEvent]
    risk_factors: list[RiskFactor]
    wave_thresholds: WaveThresholds = Field(default_factory=WaveThresholds)
    checkin_questions: list[CheckinQuestion]
    red_flags: list[RedFlagRule]
    needs: list[Need]
    relief_centre_kind: str
    script: ProfileScript
    common: CommonScript
    recheck_minutes: int = 240
    eval_personas: str = ""
    source_files: list[str] = Field(default_factory=list)

    # --- vocabulary helpers used by agents, tools and the backstop ---

    def red_flag_categories(self) -> list[str]:
        return [rule.category for rule in self.red_flags]

    def need_ids(self) -> list[str]:
        return [need.id for need in self.needs]

    def activation_for(
        self, event_name: str, vtec: str | None = None
    ) -> Literal["auto", "ask"] | None:
        """How this profile reacts to an NWS event name or VTEC code; None if it ignores it."""
        wanted = event_name.strip().lower()
        for alert in self.alert_events:
            if alert.event.lower() == wanted:
                return alert.activation
            if vtec and alert.vtec and alert.vtec.upper() == vtec.upper():
                return alert.activation
        return None

    def questions_text(self, language: Language) -> list[str]:
        return [q.text(language) for q in self.checkin_questions]

    def hazard_phrase(self, language: Language) -> str:
        return self.script.hazard_phrase_es if language == "es" else self.script.hazard_phrase_en

    def hazard_short(self, language: Language) -> str:
        return self.script.hazard_short_es if language == "es" else self.script.hazard_short_en

    def tip(self, language: Language) -> str:
        return self.script.tip_es if language == "es" else self.script.tip_en

    def common_text(self, key: str, language: Language) -> str:
        return getattr(self.common, f"{key}_{language}")


class _SharedParts(_Strict):
    common: CommonScript
    checkin_questions: list[CheckinQuestion] = Field(default_factory=list)
    red_flags: list[RedFlagRule] = Field(default_factory=list)
    needs: list[Need] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


def load_profile(source: str | Path, *, shared_file: Path | None = None) -> HazardProfile:
    """Load a hazard profile by id (looked up in the package profiles dir) or by file path.

    Args:
        source: A profile id (the YAML file's stem) or a path to a YAML file.
        shared_file: Override the shared-parts file (tests use this).
    """
    path = Path(source)
    if not path.suffix and not path.exists():
        path = PROFILES_DIR / f"{source}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"hazard profile not found: {source}")

    raw = _read_yaml(path)
    include_shared = bool(raw.get("include_shared", True))
    sources = [str(path)]

    if include_shared:
        shared_path = shared_file or SHARED_FILE
        shared = _SharedParts.model_validate(_read_yaml(shared_path))
        sources.insert(0, str(shared_path))
        merged = {
            **raw,
            "common": raw.get("common", shared.common.model_dump()),
            "checkin_questions": [q.model_dump() for q in shared.checkin_questions]
            + list(raw.get("checkin_questions", [])),
            "red_flags": [r.model_dump() for r in shared.red_flags]
            + list(raw.get("red_flags", [])),
            "needs": [n.model_dump() for n in shared.needs] + list(raw.get("needs", [])),
        }
    else:
        merged = dict(raw)
        if "common" not in merged:
            raise ValueError(
                f"{path}: include_shared is false, so the profile must define `common`"
            )

    merged["source_files"] = sources
    profile = HazardProfile.model_validate(merged)
    _check_unique(profile)
    return profile


def _check_unique(profile: HazardProfile) -> None:
    for label, ids in (
        ("checkin question", [q.id for q in profile.checkin_questions]),
        ("red flag category", profile.red_flag_categories()),
        ("need", profile.need_ids()),
        ("risk factor", [f.id for f in profile.risk_factors]),
    ):
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"profile {profile.id}: duplicate {label} ids {sorted(dupes)}")


def available_profiles() -> list[str]:
    """Ids of the profiles shipped in the package."""
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml") if not p.name.startswith("_"))
