"""Search Doorstep's recent CloudWatch logs and spans for any secret value (make scan-logs).

Every SecureString under /doorstep is read into memory, then every log event from the last
`--hours` in the runtime log group, the three Lambda log groups and `aws/spans` is downloaded and
searched locally. Nothing is sent to CloudWatch as a query, because Logs Insights query strings
are recorded in CloudTrail: searching for a secret that way would leak it. A match prints the
parameter's *name* and where it was found, never the value.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.cloud.stack import outputs, session  # noqa: E402

MIN_LENGTH = 6  # shorter values (a flag like "off") would match ordinary words


def secrets_by_name(ssm) -> dict[str, str]:
    found: dict[str, str] = {}
    for page in ssm.get_paginator("get_parameters_by_path").paginate(
        Path="/doorstep", Recursive=True, WithDecryption=True
    ):
        for p in page["Parameters"]:
            if p["Type"] == "SecureString" and len(p["Value"]) >= MIN_LENGTH:
                found[p["Name"]] = p["Value"]
                # comma-separated lists (chat ids, phone numbers): each item is a secret too
                for i, item in enumerate(v.strip() for v in p["Value"].split(",")):
                    if len(item) >= MIN_LENGTH:
                        found[f"{p['Name']}[{i}]"] = item
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=6.0)
    args = ap.parse_args()

    aws = session()
    out = outputs(aws)
    logs = aws.client("logs")
    secrets = secrets_by_name(aws.client("ssm"))
    groups = [
        f"/aws/bedrock-agentcore/runtimes/{out['RuntimeId']}-DEFAULT",
        *(
            [f"/aws/bedrock-agentcore/runtimes/{out['VoiceRuntimeArn'].rsplit('/', 1)[1]}-DEFAULT"]
            if out.get("VoiceRuntimeArn")
            else []
        ),
        "/aws/lambda/doorstep-telegram-webhook",
        "/aws/lambda/doorstep-admin-replay",
        "/aws/lambda/doorstep-alert-poller",
        "/aws/lambda/doorstep-voice-session",
        "/aws/lambda/doorstep-checkin-worker",
        "/aws/lambda/doorstep-dashboard",
        "aws/spans",
    ]
    start = int((time.time() - args.hours * 3600) * 1000)
    leaks = 0
    for group in groups:
        scanned = 0
        try:
            for page in logs.get_paginator("filter_log_events").paginate(
                logGroupName=group, startTime=start
            ):
                for event in page["events"]:
                    scanned += 1
                    for name, value in secrets.items():
                        if value in event["message"]:
                            leaks += 1
                            print(f"  LEAK: {name} in {group} / {event['logStreamName']}")
        except logs.exceptions.ResourceNotFoundException:
            print(f"  {group}: no such log group (nothing written yet)")
            continue
        print(f"  {group}: {scanned} events scanned")
    print(f"searched for {len(secrets)} secret values over the last {args.hours:g} h")
    print(f"RESULT: {'PASS' if leaks == 0 else 'FAIL'} ({leaks} matches)")
    return 0 if leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
