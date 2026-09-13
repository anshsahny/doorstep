"""Write the dashboard's generated data from the repo's own sources (no network, no AWS).

* `web/src/generated/profile.json` — labels from the active hazard profile (risk factors, red
  flags, needs, display name). The dashboard hard-codes no hazard wording; it reads this.
* `web/src/generated/policies.json` — every Cedar policy's text beside its plain English
  (`agent/policies/plain_english.yaml`).

`tests/test_policies_page.py` fails if either file is stale, so the page cannot drift.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from doorstep_agent.config import settings  # noqa: E402
from doorstep_agent.profiles import load_profile  # noqa: E402

OUT = ROOT / "web" / "src" / "generated"
POLICIES = ROOT / "agent" / "policies"
POLICY_ID = re.compile(r'@id\("([a-z0-9_]+)"\)')


def split_policies(text: str) -> list[tuple[str, str]]:
    """(id, cedar text including its leading comment) for each policy in one file."""
    found = []
    blocks = re.split(r"\n\s*\n", text.strip())
    comment: list[str] = []
    for block in blocks:
        match = POLICY_ID.search(block)
        if match is None:
            comment.append(block)
            continue
        found.append((match.group(1), "\n\n".join([*comment, block]).strip()))
        comment = []
    return found


def policies() -> list[dict]:
    english = yaml.safe_load((POLICIES / "plain_english.yaml").read_text(encoding="utf-8"))
    order = list(english)
    out = []
    for path in sorted(POLICIES.glob("*.cedar")):
        for policy_id, cedar in split_policies(path.read_text(encoding="utf-8")):
            entry = english.get(policy_id) or {}
            out.append(
                {
                    "id": policy_id,
                    "file": f"agent/policies/{path.name}",
                    "cedar": cedar,
                    "title": entry.get("title", ""),
                    "kind": entry.get("kind", ""),
                    "plain": entry.get("plain", ""),
                    "protects": entry.get("protects", ""),
                    "checked_by": [
                        t.removeprefix("test_").replace("_", " ").capitalize()
                        for t in entry.get("checked_by", [])
                    ],
                }
            )
    return sorted(out, key=lambda p: order.index(p["id"]) if p["id"] in order else len(order))


def profile() -> dict:
    p = load_profile(settings().default_profile)
    return {
        "id": p.id,
        "display_name": p.display_name,
        "risk_factors": {f.id: f.label for f in p.risk_factors},
        "red_flags": {f.category: f.label for f in p.red_flags},
        "needs": {n.id: n.label for n in p.needs},
    }


def render() -> dict[str, str]:
    return {
        "profile.json": json.dumps(profile(), indent=2, ensure_ascii=False) + "\n",
        "policies.json": json.dumps(policies(), indent=2, ensure_ascii=False) + "\n",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in render().items():
        (OUT / name).write_text(text, encoding="utf-8")
        print(f"wrote web/src/generated/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
