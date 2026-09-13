"""Gate 5: every sandbox and voice cap, the passcode lockout and the kill switch, deployed.

What it does, in order, and what it costs:

1. `POST /drills` once for real (one sandbox drill, about $0.38), then again at once: the second
   is refused by the per-IP limit. The first drill's token is used for the voice checks below.
   With `--after-drill <incident id>` (a drill this address started in the current 10-minute
   window, e.g. by `make keyboard-pass ARGS=--live`), no drill is started: the first request
   must already be refused, and the voice checks use a captain session instead. $0.
2. Every other cap is proved without spending: the counter is set to its limit in DynamoDB, one
   request is sent and must be refused with the right message, and the counter is put back
   exactly as it was (or deleted), even if the script fails. Voice refusals happen before any
   model stream opens, so they cost nothing either.
3. The kill switch is turned on, drills, voice links and answers must return 503, and it is
   turned off again (the `finally` block restores it whatever happens).

Prints a table of cap, configured value, expected status, actual status.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
TABLE = "doorstep"


class Counters:
    """Set counters for a check, and put every one back afterwards."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.saved: dict[str, int | None] = {}

    def get(self, key: str) -> int | None:
        item = self.client.get_item(
            TableName=TABLE, Key={"PK": {"S": key}, "SK": {"S": "COUNT"}}, ConsistentRead=True
        ).get("Item")
        return int(item["n"]["N"]) if item and "n" in item else None

    def set(self, key: str, n: int) -> None:
        if key not in self.saved:
            self.saved[key] = self.get(key)
        self.client.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": key},
                "SK": {"S": "COUNT"},
                "n": {"N": str(n)},
                "ttl": {"N": str(int(time.time()) + 3 * 24 * 3600)},
            },
        )

    def watch(self, key: str) -> None:
        if key not in self.saved:
            self.saved[key] = self.get(key)

    def restore(self) -> None:
        for key, n in self.saved.items():
            if n is None:
                self.client.delete_item(
                    TableName=TABLE, Key={"PK": {"S": key}, "SK": {"S": "COUNT"}}
                )
            else:
                self.set_raw(key, n)
        self.saved.clear()

    def set_raw(self, key: str, n: int) -> None:
        self.client.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": key},
                "SK": {"S": "COUNT"},
                "n": {"N": str(n)},
                "ttl": {"N": str(int(time.time()) + 3 * 24 * 3600)},
            },
        )


def wait_clear_of_boundary(seconds: int = 20) -> None:
    """Windows are clock-aligned (10 minutes, hours, days); do not straddle a boundary."""
    while True:
        now = datetime.now(UTC)
        into = (now.minute % 10) * 60 + now.second
        if 5 <= into <= 600 - seconds:
            return
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-drill", help="incident id of a drill this IP started just now")
    args = parser.parse_args()
    session = boto3.Session()
    outputs = json.loads((ROOT / "cdk.out" / "outputs.json").read_text())["Doorstep"]
    api = outputs["ApiUrl"].rstrip("/")
    ssm = session.client("ssm")
    dynamodb = session.client("dynamodb")
    caps = json.loads(ssm.get_parameter(Name="/doorstep/caps")["Parameter"]["Value"])
    ip = httpx.get("https://checkip.amazonaws.com", timeout=10).text.strip()
    passcode = (dotenv_values(ROOT / ".env").get("CAPTAIN_PASSCODE") or "").strip()
    counters = Counters(dynamodb)
    http = httpx.Client(timeout=30)
    rows: list[tuple[str, str, int, int, str]] = []
    kill = ssm.get_parameter(Name="/doorstep/kill_switch", WithDecryption=True)["Parameter"]
    kill_before, kill_type = kill["Value"], kill["Type"]
    drill: dict[str, Any] = {}

    def check(name: str, configured: Any, expected: int, response: httpx.Response) -> None:
        body = response.json() if response.content else {}
        rows.append(
            (name, str(configured), expected, response.status_code, str(body.get("error", ""))[:70])
        )

    def drills() -> httpx.Response:
        return http.post(f"{api}/drills", headers={"idempotency-key": uuid.uuid4().hex})

    try:
        # --- 1. per IP, for real -----------------------------------------------------------
        if not args.after_drill:
            wait_clear_of_boundary(60)
        now = datetime.now(UTC)
        window = f"{now:%Y%m%d%H}{now.minute // 10}"
        rate_key = f"RATE#sandbox#{ip}#{window}"
        for key in (rate_key, f"CAP#sandbox#window#{window}",
                    f"CAP#sandbox#{now:%Y%m%d}", "CAP#sandbox#total"):  # fmt: skip
            counters.watch(key)
        if args.after_drill:
            captain = http.post(f"{api}/captain/session", json={"passcode": passcode})
            drill = {
                "incident_id": args.after_drill,
                "token": captain.json().get("token", ""),
                "voice_resident": "r06",
            }
        else:
            first = drills()
            check("drill: first request starts a real drill", "-", 202, first)
            if first.status_code != 202:
                print(first.text)
                return 1
            drill = first.json()
            for key in list(counters.saved):
                counters.saved[key] = counters.get(key)  # the real drill stays counted
        second = drills()
        check("drill: per IP per 10 min", caps.get("sandbox_per_ip_per_10min"), 429, second)
        leaked = {k: counters.get(k) for k in counters.saved if k != rate_key}
        refunded = all(counters.get(k) == counters.saved[k] for k in leaked)
        rows.append(
            ("drill: refused request took no count", "-", 1, int(refunded), json.dumps(leaked)[:70])
        )

        # --- 2. the other caps, by setting the counter to its limit --------------------------
        counters.set(rate_key, 0)
        for name, key, cap in (
            ("drill: everyone per 10 min", f"CAP#sandbox#window#{window}", "sandbox_per_10min"),
            ("drill: per day", f"CAP#sandbox#{now:%Y%m%d}", "sandbox_daily"),
            ("drill: judging period total", "CAP#sandbox#total", "sandbox_total"),
        ):
            wait_clear_of_boundary()
            later = datetime.now(UTC)
            if f"{later:%Y%m%d%H}{later.minute // 10}" != window:
                # A new window would make every seeded key stale and let a real drill through.
                raise SystemExit("the 10-minute window changed mid-test; nothing spent, rerun")
            original = counters.get(key)
            counters.set(key, int(caps[cap]))
            check(name, caps[cap], 429, drills())
            counters.set_raw(key, original if original is not None else 0)
            if original is None:
                dynamodb.delete_item(TableName=TABLE, Key={"PK": {"S": key}, "SK": {"S": "COUNT"}})
        counters.restore()

        token = drill["token"]
        voice_body = {"incident_id": drill["incident_id"], "resident_id": drill["voice_resident"]}
        auth = {"authorization": f"Bearer {token}"}
        hour = datetime.now(UTC).strftime("%Y%m%d%H")
        day = datetime.now(UTC).strftime("%Y%m%d")
        time.sleep(5)  # let the drill's incident row appear
        for name, key, cap in (
            ("voice: per IP per hour", f"RATE#voice#{ip}#{hour}", "voice_per_ip_per_hour"),
            ("voice: per day", f"CAP#voice#{day}", "voice_daily"),
            ("voice: judging period total", "CAP#voice#total", "voice_total"),
        ):
            counters.watch(f"RATE#voice#{ip}#{hour}")
            counters.set(key, int(caps[cap]))
            check(
                name,
                caps[cap],
                429,
                http.post(f"{api}/voice/session", json=voice_body, headers=auth),
            )
            counters.restore()
        check(
            "voice: no session token", "-", 401, http.post(f"{api}/voice/session", json=voice_body)
        )

        # --- passcode lockout -------------------------------------------------------------
        fail_key = f"RATE#passfail#{ip}#{hour}"
        counters.set(fail_key, int(caps.get("passcode_failures_per_hour", 10)))
        locked = http.post(f"{api}/captain/session", json={"passcode": passcode})
        limit = caps.get("passcode_failures_per_hour")
        check("captain: lockout refuses even the right passcode", limit, 429, locked)
        counters.restore()
        captain = http.post(f"{api}/captain/session", json={"passcode": passcode})
        check("captain: right passcode after the lockout ends", "-", 201, captain)

        # --- 3. kill switch ---------------------------------------------------------------
        ssm.put_parameter(Name="/doorstep/kill_switch", Value="on", Type=kill_type, Overwrite=True)
        time.sleep(20)  # Lambdas cache the switch for 15 s
        check("kill switch: drills", "on", 503, drills())
        check(
            "kill switch: voice links",
            "on",
            503,
            http.post(f"{api}/voice/session", json=voice_body, headers=auth),
        )
        answer_url = f"{api}/incidents/{drill['incident_id']}/decisions/dec-001"
        answered = http.post(answer_url, json={"option_id": "handle"}, headers=auth)
        check("kill switch: answers", "on", 503, answered)
    finally:
        counters.restore()
        ssm.put_parameter(
            Name="/doorstep/kill_switch", Value=kill_before, Type=kill_type, Overwrite=True
        )

    width = max(len(r[0]) for r in rows)
    print(f"\n{'check':{width}}  cap     want  got   message")
    ok = True
    for name, configured, want, got, message in rows:
        passed = want == got
        ok &= passed
        verdict = "PASS" if passed else "FAIL"
        print(f"{name:{width}}  {configured:6}  {want:4}  {got:4}  {verdict}  {message}")
    print(f"\nsource IP as the API sees it: {ip}; kill switch restored to {kill_before!r}")
    print(f"real drill started for the per-IP check: {drill.get('incident_id', '-')}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
