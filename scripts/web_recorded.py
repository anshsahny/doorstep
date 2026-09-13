"""Save one finished drill as the dashboard's recorded drill (`web/public/recorded-*.json`).

Reads the incident's rows from DynamoDB (read-only) and shapes them with the same functions the
dashboard API uses, so the recorded board is exactly what a visitor saw. Everything is fictional;
`snapshot.mask` still runs, and the script refuses to write anything that looks like a secret or
a contact reference.

    make web-recorded ARGS=<incident id>
    uv run python scripts/web_recorded.py --from-json <rows.json>   # rows already exported
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from doorstep_api import snapshot  # noqa: E402

OUT = ROOT / "web" / "public"
FORBIDDEN = re.compile(
    r"phone_ref|family_contact_ref|telegram_chat_id_ref|CALL_ALLOWLIST|TELEGRAM_|AKIA"
)


def rows_from_dynamodb(incident_id: str) -> dict[str, dict]:
    import boto3

    client = boto3.Session().client("dynamodb")
    docs: dict[str, dict] = {}
    for pk in (f"INC#{incident_id}", "ORG#juniper-court"):
        kwargs = {
            "TableName": "doorstep",
            "KeyConditionExpression": "PK = :p",
            "ExpressionAttributeValues": {":p": {"S": pk}},
        }
        while True:
            page = client.query(**kwargs)
            for item in page["Items"]:
                if "doc" in item:
                    docs[f"{pk}|{item['SK']['S']}"] = json.loads(item["doc"]["S"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return docs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("incident", nargs="?")
    parser.add_argument("--from-json", type=Path)
    args = parser.parse_args()
    if args.from_json:
        raw = json.loads(args.from_json.read_text())
        docs = {k if "|" in k else f"INC#x|{k}": v for k, v in raw.items()}
        roster = json.loads((ROOT / "data" / "roster.json").read_text())["residents"]
        volunteers = json.loads((ROOT / "data" / "volunteers.json").read_text())["volunteers"]
        org = json.loads((ROOT / "data" / "org.json").read_text())
    elif args.incident:
        docs = rows_from_dynamodb(args.incident)
        roster = [v for k, v in docs.items() if "|RES#" in k]
        volunteers = [v for k, v in docs.items() if "|VOL#" in k]
        org = next(v for k, v in docs.items() if k.endswith("|PROFILE"))
    else:
        parser.error("give an incident id or --from-json")

    def rows(prefix: str) -> list[dict]:
        return [v for k, v in docs.items() if k.split("|", 1)[1].startswith(prefix)]

    (meta,) = rows("META")
    cases, decisions, events, messages = rows("CASE#"), rows("DEC#"), rows("EVT#"), rows("MSG#")
    board = snapshot.build(
        incident=meta,
        cases=cases,
        decisions=decisions,
        events=events,
        residents=roster,
        volunteers=volunteers,
        now=max((e["at"] for e in events), default=""),
    )
    board["events"] = [snapshot.event_view(e) for e in sorted(events, key=lambda e: e["seq"])]
    report = snapshot.mask(
        snapshot.report(
            incident=meta,
            cases=cases,
            decisions=decisions,
            events=events,
            messages=messages,
            org=org,
        )
    )
    for name, body in (("recorded-drill.json", board), ("recorded-report.json", report)):
        text = json.dumps(body, indent=1, ensure_ascii=False)
        if FORBIDDEN.search(text):
            raise SystemExit(f"refusing to write {name}: {FORBIDDEN.search(text).group(0)!r} found")
        (OUT / name).write_text(text + "\n", encoding="utf-8")
        print(f"wrote web/public/{name} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
