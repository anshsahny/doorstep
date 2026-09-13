"""The Policies page cannot drift from the policies (Gate 5; SPEC §8 says denials show there)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_export", ROOT / "scripts" / "web_export.py")
web_export = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(web_export)  # type: ignore[union-attr]


def cedar_ids() -> set[str]:
    return {
        m
        for path in (ROOT / "agent" / "policies").glob("*.cedar")
        for m in re.findall(r'@id\("([a-z0-9_]+)"\)', path.read_text(encoding="utf-8"))
    }


def test_every_policy_has_plain_english_and_nothing_else_does() -> None:
    english = yaml.safe_load((ROOT / "agent/policies/plain_english.yaml").read_text())
    assert set(english) == cedar_ids()
    for policy_id, entry in english.items():
        assert entry["title"] and entry["plain"] and entry["protects"], policy_id
        assert entry["kind"] in ("permit", "forbid"), policy_id


def test_every_named_check_is_a_real_cedar_test() -> None:
    english = yaml.safe_load((ROOT / "agent/policies/plain_english.yaml").read_text())
    tests = set(re.findall(r"^def (test_\w+)", (ROOT / "tests/test_cedar.py").read_text(), re.M))
    for policy_id, entry in english.items():
        assert entry["checked_by"], policy_id
        missing = set(entry["checked_by"]) - tests
        assert not missing, f"{policy_id} names tests that do not exist: {missing}"


def test_the_kind_in_plain_english_matches_the_cedar() -> None:
    for policy in web_export.policies():
        effect = re.search(r"^(permit|forbid)\(", policy["cedar"], re.M)
        assert effect and effect.group(1) == policy["kind"], policy["id"]


def test_each_policy_block_holds_exactly_its_own_cedar() -> None:
    exported = web_export.policies()
    assert {p["id"] for p in exported} == cedar_ids()
    for policy in exported:
        assert len(re.findall(r"@id\(", policy["cedar"])) == 1, policy["id"]


def test_the_generated_files_are_current() -> None:
    for name, text in web_export.render().items():
        on_disk = (ROOT / "web" / "src" / "generated" / name).read_text(encoding="utf-8")
        assert on_disk == text, f"run `uv run python scripts/web_export.py` ({name} is stale)"
