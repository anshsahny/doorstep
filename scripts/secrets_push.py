"""Copy Doorstep's secrets from `.env` into SSM Parameter Store (make secrets-push).

SecureString with the AWS-managed `aws/ssm` key (no Secrets Manager, no customer key: $0).
Prints each parameter name with `created`, `updated`, `unchanged` or `skipped` — never a value
and never a hash of one.

Switches (`kill_switch`, `poller_mode`, `caps`) and the Telegram webhook secret are created only
when absent, so re-running this after flipping the kill switch cannot flip it back.

    uv run python scripts/secrets_push.py [--generate-missing]

`--generate-missing` fills an empty CAPTAIN_PASSCODE / INTERNAL_HMAC_SECRET in `.env` with a
random value first (written to `.env` only; read it there).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from botocore.exceptions import ClientError
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.cloud.stack import session  # noqa: E402

PREFIX = "/doorstep"
FROM_ENV = {
    "TELEGRAM_BOT_TOKEN": "telegram/bot_token",
    "TELEGRAM_CAPTAIN_CHAT_ID": "telegram/captain_chat_id",
    "TELEGRAM_VOLUNTEER_CHAT_IDS": "telegram/volunteer_chat_ids",
    "CAPTAIN_PASSCODE": "captain_passcode",
    "INTERNAL_HMAC_SECRET": "internal_hmac_secret",
    "CALL_ALLOWLIST": "call_allowlist",
    "NWS_USER_AGENT": "nws_user_agent",
}
GENERATABLE = ("CAPTAIN_PASSCODE", "INTERNAL_HMAC_SECRET")
IF_ABSENT = {
    "kill_switch": ("String", lambda: "off"),
    "poller_mode": ("String", lambda: "observe"),
    "caps": (
        "String",
        lambda: json.dumps(
            {"per_ip_per_10min": 2, "daily": 10, "total": 60, "passcode_failures_per_hour": 10}
        ),
    ),
    "telegram/webhook_secret": ("SecureString", lambda: secrets.token_urlsafe(48)),
}


def current(ssm, name: str) -> str | None:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--generate-missing", action="store_true")
    args = ap.parse_args()

    env_path = ROOT / ".env"
    if args.generate_missing:
        values = dotenv_values(env_path)
        for key in GENERATABLE:
            if not (values.get(key) or "").strip():
                set_key(str(env_path), key, secrets.token_urlsafe(24), quote_mode="never")
                print(f"{key}: generated into .env")
    values = dotenv_values(env_path)
    ssm = session().client("ssm")
    problems = 0

    for env_name, param in FROM_ENV.items():
        name = f"{PREFIX}/{param}"
        value = (values.get(env_name) or "").strip()
        if not value or "example.com" in value:
            print(f"{name}: skipped ({env_name} is empty or still the example)")
            problems += env_name in (
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CAPTAIN_CHAT_ID",
                "CAPTAIN_PASSCODE",
            )
            continue
        existing = current(ssm, name)
        if existing == value:
            print(f"{name}: unchanged")
            continue
        ssm.put_parameter(
            Name=name, Value=value, Type="SecureString", Overwrite=True, Tier="Standard"
        )
        print(f"{name}: {'updated' if existing is not None else 'created'}")

    for param, (kind, make) in IF_ABSENT.items():
        name = f"{PREFIX}/{param}"
        if current(ssm, name) is not None:
            print(f"{name}: unchanged (kept as set)")
            continue
        ssm.put_parameter(Name=name, Value=make(), Type=kind, Tier="Standard")
        print(f"{name}: created")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
