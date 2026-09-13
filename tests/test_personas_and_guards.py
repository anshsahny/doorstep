"""The persona set for the drill, and the guard that keeps hazard words out of the agent code."""

import json
import re
from pathlib import Path

from doorstep_agent.agents.persona import load_personas
from doorstep_agent.profiles import load_profile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agent" / "doorstep_agent"

HAZARD_WORDS = [
    "heat", "heatwave", "cooling", "warming", "cold", "sweat", "sweating", "hypothermia",
    "frostbite", "air conditioning", "air-conditioning", "wildfire", "smoke",
]  # fmt: skip


def test_drill_personas_cover_the_plan_mix() -> None:
    profile = load_profile("heat")
    personas = load_personas(ROOT / profile.eval_personas)
    assert len(personas) == 12
    by_status: dict[str, int] = {}
    for p in personas:
        by_status[p.ground_truth.status] = by_status.get(p.ground_truth.status, 0) + 1
    assert by_status == {"OK": 7, "NEEDS_HELP": 2, "URGENT": 2, "NO_ANSWER": 1}
    urgent = [p for p in personas if p.ground_truth.status == "URGENT"]
    assert sorted(p.ground_truth.hidden for p in urgent) == [False, True]
    assert sum(p.behaviour == "no_answer" for p in personas) == 1
    assert all(p.fictional for p in personas)


def test_personas_match_the_roster_and_the_profile_vocabulary() -> None:
    profile = load_profile("heat")
    personas = load_personas(ROOT / profile.eval_personas)
    roster = json.loads((ROOT / "data" / "roster.json").read_text())
    residents = {r["id"]: r for r in roster["residents"]}
    assert sorted(p.resident_id for p in personas) == sorted(roster["drill_subset"])
    for p in personas:
        r = residents[p.resident_id]
        assert p.language == r["language"], p.id
        assert p.display_name == r["name"], p.id
        assert set(p.ground_truth.red_flags) <= set(profile.red_flag_categories()), p.id
        assert set(p.ground_truth.needs) <= set(profile.need_ids()), p.id


def test_no_hazard_words_in_the_agent_package_source() -> None:
    """SPEC §3a: nothing hazard-specific in agents, tools, policies or prompts; only in profiles."""
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in HAZARD_WORDS) + r")\b", re.I)
    offenders = []
    for path in sorted(
        [*PACKAGE.rglob("*.py"), *(ROOT / "voice" / "doorstep_voice").rglob("*.py")]
    ):
        if path.name == "config.py":
            continue  # configuration picks the active profile; it is not agent logic
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    for path in sorted((ROOT / "agent" / "policies").glob("*.cedar")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "hazard-specific words in shared code:\n" + "\n".join(offenders)
