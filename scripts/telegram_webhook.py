"""Point the bot at the deployed webhook, or back to long polling (make telegram-webhook).

    set     setWebhook to <ApiUrl>/telegram/webhook with the SSM secret token, taps only
    delete  remove the webhook (local `make telegram-drill` long polling works again)
    info    getWebhookInfo, with the URL shown as host only

The bot token and the secret are read into memory from SSM; neither is printed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from scripts.cloud.stack import outputs, session  # noqa: E402


def secret(ssm, name: str) -> str:
    return ssm.get_parameter(Name=f"/doorstep/{name}", WithDecryption=True)["Parameter"]["Value"]


def main(action: str) -> int:
    ssm = session().client("ssm")
    token = secret(ssm, "telegram/bot_token")

    def call(method: str, **params) -> dict:
        reply = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=20)
        data = reply.json()
        if not data.get("ok"):
            raise SystemExit(f"{method} failed: {data.get('description')}")
        return data["result"]

    if action == "set":
        url = outputs()["ApiUrl"].rstrip("/") + "/telegram/webhook"
        call(
            "setWebhook",
            url=url,
            secret_token=secret(ssm, "telegram/webhook_secret"),
            allowed_updates=["callback_query"],
            drop_pending_updates=True,
        )
        print(f"webhook set to https://{urlparse(url).netloc}/telegram/webhook")
    elif action == "delete":
        call("deleteWebhook", drop_pending_updates=False)
        print("webhook removed; long polling works again")
    info = call("getWebhookInfo")
    host = urlparse(info.get("url") or "").netloc or "(none)"
    print(
        f"webhook host: {host} | pending: {info.get('pending_update_count')} | "
        f"last error: {info.get('last_error_message') or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "info"))
