"""Smoke test 05: a Telegram message with inline buttons; the button tap comes back by long polling.

Default: sends a captain-style message with three buttons to TELEGRAM_CAPTAIN_CHAT_ID, waits up
to two minutes for the tap, acknowledges it and edits the message to show the choice.

--whoami: lists the chat IDs seen in recent updates. Open the bot in Telegram, tap Start, send
any message, then run this to learn your chat ID (no third-party "get my ID" bot needed).

Pass criteria: a callback_query for the sent message arrives and is acknowledged.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import httpx

from _common import env, fail, info, ok, passed, step

BUTTONS = [
    ("I'm handling it", "smoke:handling"),
    ("Send Tom (0.3 mi)", "smoke:send_tom"),
    ("Call her daughter", "smoke:call_family"),
]
LABELS = {data: text for text, data in BUTTONS}
WAIT_S = 120
POLL_TIMEOUT_S = 30
ALLOWED_UPDATES = ["message", "callback_query"]

MESSAGE = (
    "Doorstep smoke test (Phase 0)\n\n"
    "Mrs. Chen, 81, lives alone: said she's dizzy and confused. Told her to call 911.\n"
    "(fictional resident)\n\n"
    "Tap a button to confirm the round trip."
)


class Bot:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = httpx.Client(timeout=POLL_TIMEOUT_S + 10)

    def call(self, method: str, **params: Any) -> Any:
        try:
            response = self._http.post(f"{self._base}/{method}", json=params)
            data = response.json()
        except httpx.HTTPError as exc:
            # Never echo the URL: it contains the bot token.
            fail(f"{method}: {type(exc).__name__}")
        except ValueError:
            fail(f"{method}: non-JSON response (HTTP {response.status_code})")
        if not data.get("ok"):
            fail(f"{method} failed: {data.get('description')}")
        return data["result"]


def chat_of(update: dict[str, Any]) -> dict[str, Any] | None:
    msg = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
        or (update.get("callback_query") or {}).get("message")
    )
    return msg.get("chat") if msg else None


def whoami(bot: Bot) -> None:
    updates = bot.call("getUpdates", timeout=0, allowed_updates=ALLOWED_UPDATES)
    seen: dict[int, str] = {}
    for update in updates:
        chat = chat_of(update)
        if chat:
            label = chat.get("title") or " ".join(
                p for p in (chat.get("first_name"), chat.get("last_name")) if p
            )
            handle = f" @{chat['username']}" if chat.get("username") else ""
            seen[chat["id"]] = f"{chat.get('type')}: {label}{handle}"
    if not seen:
        info("No updates yet. Open the bot in Telegram, tap Start, send any message, then rerun.")
        return
    for chat_id, desc in seen.items():
        ok(f"chat_id={chat_id}  {desc}")
    info("Put your own chat_id in .env as TELEGRAM_CAPTAIN_CHAT_ID")


def ping(bot: Bot, chat_id: str) -> None:
    webhook = bot.call("getWebhookInfo")
    if webhook.get("url"):
        fail("a webhook is set on this bot; long polling needs it removed (deleteWebhook)", code=2)

    # Skip anything that arrived before this run.
    backlog = bot.call("getUpdates", timeout=0, allowed_updates=ALLOWED_UPDATES)
    offset = backlog[-1]["update_id"] + 1 if backlog else None

    step(f"Sending the message with {len(BUTTONS)} inline buttons to chat {chat_id}")
    keyboard = {
        "inline_keyboard": [[{"text": text, "callback_data": data}] for text, data in BUTTONS]
    }
    sent = bot.call("sendMessage", chat_id=chat_id, text=MESSAGE, reply_markup=keyboard)
    message_id = sent["message_id"]
    ok(f"sent message_id={message_id}")

    step(f"Waiting up to {WAIT_S}s for a button tap (long polling)")
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_S, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            params["offset"] = offset
        for update in bot.call("getUpdates", **params):
            offset = update["update_id"] + 1
            query = update.get("callback_query")
            if not query or (query.get("message") or {}).get("message_id") != message_id:
                continue
            data = query.get("data", "")
            label = LABELS.get(data, data)
            who = query.get("from", {})
            ok(
                f"callback received: data={data!r} "
                f"from {who.get('first_name')} (id {who.get('id')})"
            )
            bot.call("answerCallbackQuery", callback_query_id=query["id"], text=f"Got it: {label}")
            bot.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=f"Doorstep smoke test (Phase 0)\n\nYou chose: {label}",
            )
            # Mark the update as consumed so it is not redelivered to the next poll.
            bot.call("getUpdates", offset=offset, timeout=0, allowed_updates=ALLOWED_UPDATES)
            passed(f"Telegram round trip: message + inline buttons + callback ({label})")
    fail(f"no button tap within {WAIT_S}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--whoami", action="store_true", help="list chat IDs from recent updates")
    args = parser.parse_args()

    bot = Bot(env("TELEGRAM_BOT_TOKEN"))
    me = bot.call("getMe")
    ok(f"bot @{me.get('username')} (id {me.get('id')}) authenticated")

    if args.whoami:
        whoami(bot)
        return
    ping(bot, env("TELEGRAM_CAPTAIN_CHAT_ID"))


if __name__ == "__main__":
    main()
