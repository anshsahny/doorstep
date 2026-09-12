"""Domain models (SPEC §6.1, §6.3, §10).

Pydantic models shared by the agents, tools, store, policies and the drill runner. Health details
stay minimal by design: a resident carries flags such as `power_dependent`, never diagnoses.
Nothing hazard-specific lives here; hazard vocabularies (needs, red-flag categories) come from the
active hazard profile and are validated against it at runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["en", "es"]


def as_str_list(value: Any) -> Any:
    """Coerce the shapes a model tends to emit for a list of ids: 'a', 'a, b', None, ['a']."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return value


def as_severity(value: Any) -> Any:
    if isinstance(value, str):
        cleaned = value.strip().capitalize()
        return cleaned if cleaned in Severity.__args__ else "Unknown"
    return value


def as_text(value: Any) -> Any:
    """Coerce what a model emits for a free-text field: None, a list of strings, a number."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v is not None)
    if isinstance(value, (int, float, bool)):
        return str(value)
    return value


Mode = Literal["live", "drill", "sandbox"]
Channel = Literal["simulated", "browser", "phone"]
Severity = Literal["Minor", "Moderate", "Severe", "Extreme", "Unknown"]


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- static org data --------------------------------------------------------------------------


class Consent(BaseModel):
    calls: bool
    family: bool
    share_with_volunteer: bool


class Resident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    first_name: str
    unit: str | None = None
    building: str
    address_label: str
    lat: float
    lng: float
    phone_ref: str | None = None
    language: Language = "en"
    interpreter_preference: str | None = None
    age_band: str
    lives_alone: bool
    has_ac: bool
    power_dependent: bool
    mobility_limited: bool
    chronic_flag: bool
    prior_no_answer: bool = False
    consent: Consent
    family_contact_ref: str | None = None
    notes: list[str] = Field(default_factory=list)
    extra: dict[str, bool | int | str] = Field(default_factory=dict)
    fictional: bool = True

    def field_value(self, name: str) -> Any:
        """Read a risk-factor field by name: a model field first, then `extra`.

        `extra` lets another hazard profile weight flags the SPEC schema does not name
        without a code change.
        """
        if name in type(self).model_fields and name != "extra":
            return getattr(self, name)
        return self.extra.get(name)


class Volunteer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: Literal["captain", "volunteer"]
    telegram_chat_id_ref: str
    lat: float
    lng: float
    available: bool = True
    notes: str = ""


class ReliefCentre(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: list[str]
    address: str
    lat: float
    lng: float
    hours_sample: str = ""
    notes: str = ""


class LatLng(BaseModel):
    lat: float
    lng: float


class OrgProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str = ""
    location: LatLng
    timezone: str
    nws: dict[str, str] = Field(default_factory=dict)
    captain_id: str
    phone_tree_baseline: dict[str, Any] = Field(default_factory=dict)
    fictional: bool = True


# --- alerts -----------------------------------------------------------------------------------


class Alert(BaseModel):
    """One NWS alert in the shape Doorstep consumes (from the live API or a replay fixture)."""

    id: str
    event: str
    severity: Severity = "Unknown"
    urgency: str = ""
    certainty: str = ""
    headline: str = ""
    description: str = ""
    area_desc: str = ""
    onset: str = ""
    expires: str = ""
    vtec: str | None = None
    ugc: list[str] = Field(default_factory=list)
    sender: str = ""

    @classmethod
    def from_nws_feature(cls, feature: dict[str, Any], *, vtec: str | None = None) -> Alert:
        p = feature.get("properties", feature)
        params = p.get("parameters") or {}
        vtec_list = params.get("VTEC") or []
        return cls(
            id=str(p.get("id", "")),
            event=p.get("event", ""),
            severity=p.get("severity") if p.get("severity") in Severity.__args__ else "Unknown",
            urgency=p.get("urgency", "") or "",
            certainty=p.get("certainty", "") or "",
            headline=p.get("headline", "") or "",
            description=p.get("description", "") or "",
            area_desc=p.get("areaDesc", "") or "",
            onset=p.get("onset", "") or "",
            expires=p.get("expires", "") or p.get("ends", "") or "",
            vtec=vtec or (vtec_list[0] if vtec_list else None),
            ugc=list((p.get("geocode") or {}).get("UGC") or []),
            sender=p.get("senderName", "") or "",
        )

    @classmethod
    def from_fixture(cls, doc: dict[str, Any]) -> Alert:
        return cls.from_nws_feature(doc["features"][0], vtec=doc.get("vtec"))

    def vtec_code(self) -> str | None:
        """'/O.NEW.KPQR.EH.W.0001.…/' -> 'EH.W'."""
        if not self.vtec:
            return None
        parts = self.vtec.strip("/").split(".")
        return f"{parts[3]}.{parts[4]}" if len(parts) >= 5 else None


class AlertAssessment(BaseModel):
    """Structured output of the alert_assessor agent (SPEC §6)."""

    activate: bool = Field(
        description="True if the alert matches the active hazard profile and covers the org"
    )
    ask_captain: bool = Field(
        default=False,
        description="True when the profile says this event level needs the captain's confirmation",
    )
    severity: Severity = Field(description="Severity to run the incident at")
    hazards: list[str] = Field(default_factory=list, description="Profile ids that matched")
    window: str = Field(default="", description="When the hazard is in effect, in plain words")
    rationale: str = Field(description="One or two sentences explaining the decision")

    @field_validator("hazards", mode="before")
    @classmethod
    def _hazards_list(cls, value: Any) -> Any:
        return as_str_list(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: Any) -> Any:
        return as_severity(value)

    @field_validator("window", "rationale", mode="before")
    @classmethod
    def _texts(cls, value: Any) -> Any:
        return as_text(value)


# --- triage -----------------------------------------------------------------------------------


class RiskScore(BaseModel):
    resident_id: str
    points: int
    factors: list[str] = Field(default_factory=list, description="Risk-factor ids that applied")
    wave: int


class WaveAdjustment(BaseModel):
    resident_id: str
    from_wave: int
    to_wave: int
    reason: str


class CallWave(BaseModel):
    wave: int
    resident_ids: list[str]


class CallPlan(BaseModel):
    """Structured output of the triage agent (SPEC §6)."""

    waves: list[CallWave] = Field(description="Wave 1 first. Every resident appears exactly once.")
    adjustments: list[WaveAdjustment] = Field(
        default_factory=list,
        description="Residents moved up one wave with a reason. Nobody may move down.",
    )
    notes: str = Field(default="", description="One or two sentences for the captain")

    @field_validator("waves", mode="before")
    @classmethod
    def _waves(cls, value: Any) -> Any:
        """Accept [[ids], [ids], [ids]] or {"1": [ids], ...} as well as the proper shape."""
        if isinstance(value, dict):
            value = [{"wave": k, "resident_ids": v} for k, v in value.items()]
        if not isinstance(value, list):
            return value
        fixed = []
        for i, wave in enumerate(value, start=1):
            if isinstance(wave, CallWave):
                fixed.append(wave)
            elif isinstance(wave, dict):
                wave = dict(wave)
                wave["resident_ids"] = as_str_list(wave.get("resident_ids"))
                raw = str(wave.get("wave", i)).strip().lower().replace("wave", "").strip()
                wave["wave"] = int(raw) if raw.isdigit() else i
                fixed.append(wave)
            else:
                fixed.append({"wave": i, "resident_ids": as_str_list(wave)})
        return fixed

    @field_validator("adjustments", mode="before")
    @classmethod
    def _adjustments(cls, value: Any) -> Any:
        return [] if value is None or isinstance(value, str) else value

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, value: Any) -> Any:
        return as_text(value)

    def ordered_ids(self) -> list[str]:
        return [
            rid for wave in sorted(self.waves, key=lambda w: w.wave) for rid in wave.resident_ids
        ]


# --- check-ins --------------------------------------------------------------------------------


class CheckinStatus(StrEnum):
    OK = "OK"
    NEEDS_HELP = "NEEDS_HELP"
    URGENT = "URGENT"
    NO_ANSWER = "NO_ANSWER"
    UNCLEAR = "UNCLEAR"


class BackstopOutcome(BaseModel):
    """What the deterministic red-flag backstop did with a model classification."""

    matched_categories: list[str] = Field(default_factory=list)
    matched_phrases: list[str] = Field(default_factory=list)
    model_status: CheckinStatus
    final_status: CheckinStatus
    raised: bool = False
    disagreement: Literal["none", "backstop_raised", "model_urgent_no_match"] = "none"


class CheckinResult(BaseModel):
    """Classification of one check-in (SPEC §6.1)."""

    status: CheckinStatus
    needs: list[str] = Field(default_factory=list, description="Need ids from the hazard profile")
    red_flags: list[str] = Field(
        default_factory=list, description="Red-flag categories from the hazard profile"
    )
    indoor_temp_hint: str | None = Field(
        default=None, description="What they said about conditions"
    )
    language: Language = "en"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    key_quote: str = Field(default="", description="The resident's most telling words, verbatim")
    summary: str = Field(default="", description="One sentence for the captain")
    flagged_mid_call: bool = False
    backstop: BackstopOutcome | None = None


class ConversationTurn(BaseModel):
    speaker: Literal["agent", "resident"]
    text: str


class CheckinAttempt(BaseModel):
    attempt: int
    channel: Channel
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    answered: bool = True
    transcript: list[ConversationTurn] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    urgent_flags: list[str] = Field(
        default_factory=list, description="Reasons the agent flagged urgent mid-call"
    )
    agent_summary: str = ""
    result: CheckinResult | None = None

    def resident_text(self) -> str:
        return "\n".join(t.text for t in self.transcript if t.speaker == "resident")


# --- cases and incidents ------------------------------------------------------------------------


class CaseState(StrEnum):
    QUEUED = "QUEUED"
    CALLING = "CALLING"
    OK = "OK"
    NEEDS_HELP = "NEEDS_HELP"
    URGENT = "URGENT"
    NO_ANSWER = "NO_ANSWER"
    UNCLEAR = "UNCLEAR"
    ASSIGNED = "ASSIGNED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class Transition(BaseModel):
    from_state: CaseState
    to_state: CaseState
    at: datetime
    reason: str = ""


class ResidentCase(BaseModel):
    incident_id: str
    resident_id: str
    state: CaseState = CaseState.QUEUED
    risk: RiskScore
    attempts: int = 0
    attempt_log: list[CheckinAttempt] = Field(default_factory=list)
    results: list[CheckinResult] = Field(default_factory=list)
    assigned_volunteer: str | None = None
    outcome: str | None = None
    next_action_at: datetime | None = None
    recheck_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    history: list[Transition] = Field(default_factory=list)

    @property
    def latest_result(self) -> CheckinResult | None:
        return self.results[-1] if self.results else None


class Incident(BaseModel):
    id: str
    org_id: str
    mode: Mode
    profile_id: str
    alert: Alert
    status: Literal["assessing", "not_activated", "awaiting_captain", "active", "closed"] = (
        "assessing"
    )
    assessment: AlertAssessment | None = None
    call_plan: CallPlan | None = None
    started_at: datetime = Field(default_factory=utcnow)


class DecisionOption(BaseModel):
    id: str
    label: str
    action: str = Field(description="Tool or verb the runner executes if chosen")
    args: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    """A pending or answered human decision (SPEC §4)."""

    id: str
    incident_id: str
    resident_id: str | None
    name: str = Field(description="Namespaced interrupt name, e.g. doorstep-urgent-red-flag")
    reason: str
    options: list[DecisionOption]
    status: Literal["pending", "answered"] = "pending"
    responder: str | None = None
    response: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    responded_at: datetime | None = None


AuditType = Literal[
    "incident",
    "assessment",
    "triage",
    "checkin",
    "classification",
    "backstop",
    "tool_call",
    "policy",
    "decision",
    "note",
]


class AuditEvent(BaseModel):
    """One row of the incident audit log (SPEC §10 EVT rows)."""

    seq: int
    incident_id: str
    at: datetime = Field(default_factory=utcnow)
    actor: str = Field(description="agent:<name>, system:<component>, captain:<id>, volunteer:<id>")
    type: AuditType
    resident_id: str | None = None
    tool: str | None = None
    input_summary: str = ""
    policy_decision: Literal["allow", "deny"] | None = None
    reason: str = ""
    rationale: str = ""
    data: dict[str, Any] = Field(default_factory=dict)

    def line(self) -> str:
        """One human-readable line for the terminal board and logs."""
        bits = [f"#{self.seq:03d}", self.at.strftime("%H:%M:%S"), self.actor, self.type]
        if self.resident_id:
            bits.append(self.resident_id)
        if self.tool:
            bits.append(self.tool)
        if self.policy_decision:
            bits.append(self.policy_decision.upper())
        text = " ".join(bits)
        detail = self.reason or self.rationale or self.input_summary
        return f"{text}: {detail}" if detail else text
