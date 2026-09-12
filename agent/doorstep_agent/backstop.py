"""Deterministic red-flag backstop (SPEC §3 F4, §6.1).

The model classifies each check-in; this module then scans the resident's own words for the
red-flag phrases of the active hazard profile. It can only raise a status to URGENT and add
categories. It never lowers a status and never removes anything the model found. Every
disagreement between the model and the backstop is recorded on the result so the audit log can
explain it.

Matching rules:
- case-insensitive and accent-insensitive, on the resident's turns only (never the agent's);
- a single-word phrase is skipped when a negation word appears within the three words before it
  ("I'm not dizzy"); multi-word phrases always match ("not sure what day it is").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import BackstopOutcome, CheckinResult, CheckinStatus
from .profiles import HazardProfile

NEGATIONS = frozenset(
    {
        "not", "no", "never", "nothing", "isn't", "isnt", "aren't", "arent", "wasn't", "wasnt",
        "don't", "dont", "doesn't", "doesnt", "didn't", "didnt", "haven't", "havent", "without",
        "ni", "nunca", "sin", "nada", "tampoco",
    }
)  # fmt: skip

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class RedFlagMatch:
    category: str
    phrase: str


def normalize(text: str) -> str:
    """Lowercase, strip accents, straighten apostrophes and collapse whitespace."""
    text = text.replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _negated(text: str, start: int) -> bool:
    before = text[:start]
    words = _WORD.findall(before)[-3:]
    return any(w in NEGATIONS for w in words)


def find_red_flags(resident_text: str, profile: HazardProfile) -> list[RedFlagMatch]:
    """All red-flag phrases from the profile found in the resident's words."""
    text = normalize(resident_text)
    if not text:
        return []
    matches: list[RedFlagMatch] = []
    for rule in profile.red_flags:
        hit = _first_phrase_hit(text, rule.phrases())
        if hit is not None:
            matches.append(RedFlagMatch(category=rule.category, phrase=hit))
    return matches


def _first_phrase_hit(text: str, phrases: list[str]) -> str | None:
    """The first phrase of a rule found in the text (one match per rule is enough)."""
    for raw in phrases:
        phrase = normalize(raw)
        if not phrase:
            continue
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")
        single_word = " " not in phrase
        for m in pattern.finditer(text):
            if single_word and _negated(text, m.start()):
                continue
            return raw
    return None


def apply_backstop(
    draft: CheckinResult, resident_text: str, profile: HazardProfile
) -> CheckinResult:
    """Return a new result that is never less severe than `draft`.

    - If any red-flag phrase matches: status becomes URGENT (if it was not already) and the
      matched categories are added to the model's red flags.
    - If nothing matches: status and red flags are left exactly as the model set them.
    """
    matches = find_red_flags(resident_text, profile)
    categories: list[str] = []
    for m in matches:
        if m.category not in categories:
            categories.append(m.category)

    final = draft.model_copy(deep=True)
    raised = False
    disagreement: str = "none"
    if matches:
        for category in categories:
            if category not in final.red_flags:
                final.red_flags.append(category)
        if draft.status != CheckinStatus.URGENT:
            final.status = CheckinStatus.URGENT
            raised = True
            disagreement = "backstop_raised"
    elif draft.status == CheckinStatus.URGENT:
        disagreement = "model_urgent_no_match"

    final.backstop = BackstopOutcome(
        matched_categories=categories,
        matched_phrases=[m.phrase for m in matches],
        model_status=draft.status,
        final_status=final.status,
        raised=raised,
        disagreement=disagreement,  # type: ignore[arg-type]
    )
    return final
