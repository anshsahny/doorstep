"""Generate the FICTIONAL 48-resident roster for the Juniper Court demo (SPEC §10, PLAN Phase 1).

Deterministic: the first 12 residents are hand-authored and match the 12 drill personas in
`evals/personas/heat/`; the other 36 are generated from a fixed seed. Names are invented, unit and
block labels are fictional, coordinates are jittered around the fictional org location, and no
phone numbers or contact details are stored (only references to environment variables or labels).

Run: uv run python scripts/gen_roster.py   -> writes data/roster.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "roster.json"
ORG_LAT, ORG_LNG = 45.4855, -122.5945
SEED = 2021

# Hand-authored drill subset. Scores use the heat profile weights (SPEC §6.2).
DRILL_RESIDENTS: list[dict] = [
    dict(
        id="r01",
        name="Rose Whitaker",
        unit="3C",
        building="Juniper Court",
        language="en",
        age_band="80+",
        lives_alone=True,
        has_ac=False,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=True,
        prior_no_answer=False,
        family_contact_ref="contact:r01-daughter",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["Hard of hearing: speak slowly and confirm by repeating back."],
        phone_ref="env:CALL_ALLOWLIST[0]",
    ),
    dict(
        id="r02",
        name="Walter Brandt",
        unit="2A",
        building="Juniper Court",
        language="en",
        age_band="80+",
        lives_alone=True,
        has_ac=True,
        power_dependent=False,
        mobility_limited=True,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=True),
        notes=["Uses a walker."],
    ),
    dict(
        id="r03",
        name="Luis Ortiz",
        unit=None,
        building="SE 66th Ave (north block)",
        language="es",
        age_band="70-79",
        lives_alone=True,
        has_ac=False,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref="contact:r03-son",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["Prefers Spanish. Has a working fan."],
    ),
    dict(
        id="r04",
        name="Dolores Fenn",
        unit="1B",
        building="Juniper Court",
        language="en",
        age_band="70-79",
        lives_alone=True,
        has_ac=False,
        power_dependent=False,
        mobility_limited=True,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=True),
        notes=[],
    ),
    dict(
        id="r05",
        name="Harold Kimura",
        unit="4D",
        building="Juniper Court",
        language="en",
        age_band="80+",
        lives_alone=True,
        has_ac=False,
        power_dependent=True,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=True,
        family_contact_ref="contact:r05-niece",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["Needs power for a medical device.", "Often leaves the phone off the hook."],
    ),
    dict(
        id="r06",
        name="Mei-Ling Chan",
        unit="2C",
        building="Juniper Court",
        language="en",
        interpreter_preference="yue",
        age_band="70-79",
        lives_alone=True,
        has_ac=True,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref="contact:r06-grandson",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["English with a Cantonese interpreter preferred for anything complicated."],
    ),
    dict(
        id="r07",
        name="Gloria Reyes",
        unit=None,
        building="SE Raymond St (east block)",
        language="es",
        age_band="60-69",
        lives_alone=False,
        has_ac=True,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=True),
        notes=["Lives with her daughter's family."],
    ),
    dict(
        id="r08",
        name="Frank Delaney",
        unit=None,
        building="SE 67th Ave (south block)",
        language="en",
        age_band="70-79",
        lives_alone=True,
        has_ac=False,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=True,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=False),
        notes=["Does not like being checked on; keep it short."],
    ),
    dict(
        id="r09",
        name="Evelyn Marsh",
        unit="3A",
        building="Juniper Court",
        language="en",
        age_band="80+",
        lives_alone=True,
        has_ac=True,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref="contact:r09-son",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["Hard of hearing: speak slowly and confirm by repeating back."],
    ),
    dict(
        id="r10",
        name="George Papadakis",
        unit=None,
        building="SE Knapp St (west block)",
        language="en",
        age_band="70-79",
        lives_alone=False,
        has_ac=False,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=True,
        prior_no_answer=False,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=True),
        notes=["Lives with his wife. Likes to chat."],
    ),
    dict(
        id="r11",
        name="Anita Sorensen",
        unit=None,
        building="SE 66th Ave (south block)",
        language="en",
        age_band="60-69",
        lives_alone=True,
        has_ac=True,
        power_dependent=False,
        mobility_limited=False,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref=None,
        consent=dict(calls=True, family=False, share_with_volunteer=True),
        notes=["Works from home; busy."],
    ),
    dict(
        id="r12",
        name="Ben Okoro",
        unit=None,
        building="SE Raymond St (west block)",
        language="en",
        age_band="60-69",
        lives_alone=True,
        has_ac=False,
        power_dependent=False,
        mobility_limited=True,
        chronic_flag=False,
        prior_no_answer=False,
        family_contact_ref="contact:r12-sister",
        consent=dict(calls=True, family=True, share_with_volunteer=True),
        notes=["Wheelchair user; ground floor."],
    ),
]

FIRST_EN = [
    "Margaret",
    "Arthur",
    "Joan",
    "Leonard",
    "Patricia",
    "Howard",
    "Ruth",
    "Norman",
    "Barbara",
    "Eugene",
    "Shirley",
    "Raymond",
    "Doris",
    "Clifford",
    "Beverly",
    "Wallace",
    "Lorraine",
    "Marvin",
    "Phyllis",
    "Vernon",
    "Gladys",
    "Elmer",
    "June",
    "Cecil",
    "Hazel",
    "Lloyd",
    "Bernice",
    "Otis",
    "Irene",
    "Gordon",
    "Mabel",
    "Stanley",
]
LAST_EN = [
    "Harris",
    "Lindqvist",
    "Bauer",
    "Ferreira",
    "Nakamura",
    "O'Connell",
    "Petrov",
    "Jensen",
    "Adeyemi",
    "Kowalski",
    "Bright",
    "Halvorsen",
    "Mendes",
    "Schulz",
    "Tanaka",
    "Byrne",
    "Novak",
    "Larsen",
    "Achebe",
    "Weber",
    "Fitzgerald",
    "Andersson",
    "Costa",
    "Mueller",
    "Sato",
    "Dunn",
    "Horvath",
    "Kristiansen",
    "Bishop",
    "Olsen",
    "Yamada",
    "Gallagher",
]
FIRST_ES = ["Carmen", "Ramón", "Teresa", "Alfonso"]
LAST_ES = ["Villanueva", "Castillo", "Herrera", "Domínguez"]
FIRST_YUE = ["Wai-Man"]
LAST_YUE = ["Leung"]

BLOCKS = [
    "SE 66th Ave (north block)",
    "SE 66th Ave (south block)",
    "SE 67th Ave (north block)",
    "SE 67th Ave (south block)",
    "SE Raymond St (east block)",
    "SE Raymond St (west block)",
    "SE Knapp St (east block)",
    "SE Knapp St (west block)",
]
JC_UNITS = [f"{floor}{letter}" for floor in (1, 2, 3, 4) for letter in "ABCD"]


def _generated(rng: random.Random, used_units: set[str]) -> list[dict]:
    residents: list[dict] = []
    free_units = [u for u in JC_UNITS if u not in used_units]
    rng.shuffle(free_units)
    # Language plan for the 36: 4 Spanish, 1 Cantonese-interpreter, 31 English.
    langs = ["es"] * 4 + ["yue"] * 1 + ["en"] * 31
    rng.shuffle(langs)
    en_names = list(zip(FIRST_EN, LAST_EN, strict=True))
    rng.shuffle(en_names)
    es_names = list(zip(FIRST_ES, LAST_ES, strict=True))
    for i, lang in enumerate(langs, start=13):
        if lang == "es":
            first, last = es_names.pop()
        elif lang == "yue":
            first, last = FIRST_YUE[0], LAST_YUE[0]
        else:
            first, last = en_names.pop()
        in_building = bool(free_units) and rng.random() < 0.30
        unit = free_units.pop() if in_building else None
        age_band = rng.choices(["60-69", "70-79", "80+"], weights=[35, 40, 25])[0]
        lives_alone = rng.random() < 0.40
        has_ac = rng.random() > 0.35
        power_dependent = rng.random() < 0.06
        family = rng.random() < 0.5
        residents.append(
            dict(
                id=f"r{i:02d}",
                name=f"{first} {last}",
                unit=unit,
                building="Juniper Court" if unit else rng.choice(BLOCKS),
                language="en" if lang == "yue" else lang,
                interpreter_preference="yue" if lang == "yue" else None,
                age_band=age_band,
                lives_alone=lives_alone,
                has_ac=has_ac,
                power_dependent=power_dependent,
                mobility_limited=rng.random() < 0.20,
                chronic_flag=rng.random() < 0.30,
                prior_no_answer=rng.random() < 0.10,
                family_contact_ref=f"contact:r{i:02d}-family" if family else None,
                consent=dict(calls=True, family=family, share_with_volunteer=rng.random() < 0.9),
                notes=[],
            )
        )
    return residents


def _finish(resident: dict, rng: random.Random) -> dict:
    """Fill derived and constant fields; keep the key order stable."""
    first_name = resident["name"].split()[0]
    lat = round(ORG_LAT + rng.uniform(-0.0030, 0.0030), 5)
    lng = round(ORG_LNG + rng.uniform(-0.0040, 0.0040), 5)
    if resident.get("unit"):
        # Everyone in the building shares (roughly) one spot.
        lat, lng = round(ORG_LAT + 0.0004, 5), round(ORG_LNG - 0.0006, 5)
    address_label = (
        f"Juniper Court, Unit {resident['unit']}" if resident.get("unit") else resident["building"]
    )
    return {
        "id": resident["id"],
        "name": resident["name"],
        "first_name": first_name,
        "unit": resident.get("unit"),
        "building": resident["building"],
        "address_label": address_label,
        "lat": lat,
        "lng": lng,
        "phone_ref": resident.get("phone_ref"),
        "language": resident["language"],
        "interpreter_preference": resident.get("interpreter_preference"),
        "age_band": resident["age_band"],
        "lives_alone": resident["lives_alone"],
        "has_ac": resident["has_ac"],
        "power_dependent": resident["power_dependent"],
        "mobility_limited": resident["mobility_limited"],
        "chronic_flag": resident["chronic_flag"],
        "prior_no_answer": resident["prior_no_answer"],
        "consent": resident["consent"],
        "family_contact_ref": resident.get("family_contact_ref"),
        "notes": resident.get("notes", []),
        "extra": {},
        "fictional": True,
    }


def main() -> None:
    rng = random.Random(SEED)
    used_units = {r["unit"] for r in DRILL_RESIDENTS if r.get("unit")}
    residents = [_finish(r, rng) for r in DRILL_RESIDENTS] + [
        _finish(r, rng) for r in _generated(rng, used_units)
    ]
    assert len(residents) == 48, len(residents)
    assert len({r["id"] for r in residents}) == 48
    doc = {
        "_note": (
            "FICTIONAL roster for the Doorstep demo, generated by scripts/gen_roster.py with a "
            "fixed seed. Every person, unit and block here is invented. No phone numbers or "
            "contact details are stored: phone_ref and family_contact_ref are references "
            "resolved at runtime, never values."
        ),
        "fictional": True,
        "org_id": "juniper-court",
        "drill_subset": [r["id"] for r in DRILL_RESIDENTS],
        "residents": residents,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    alone = sum(r["lives_alone"] for r in residents)
    no_ac = sum(not r["has_ac"] for r in residents)
    es = sum(r["language"] == "es" for r in residents)
    yue = sum(r["interpreter_preference"] == "yue" for r in residents)
    power = sum(r["power_dependent"] for r in residents)
    in_jc = sum(bool(r["unit"]) for r in residents)
    print(
        f"wrote {OUT.relative_to(ROOT)}: 48 residents | alone {alone} | no AC {no_ac} | "
        f"Spanish {es} | Cantonese-interpreter {yue} | power-dependent {power} | "
        f"in Juniper Court {in_jc}"
    )


if __name__ == "__main__":
    main()
