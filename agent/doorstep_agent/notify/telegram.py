"""Telegram as a Doorstep channel: inline buttons out, taps back (PLAN Phase 2 task 4).

Long polling here, a webhook in Phase 3 — either way this module only renders and routes. Every
tap goes to `respond_to_decision`, which owns identity, idempotency and what a choice does, so
the webhook version changes how updates arrive and nothing about what they mean.

Two safety rules are enforced here rather than left to the caller:

* messages go only to chat ids that a roster member resolves to, because a chat id typo should
  bounce rather than tell a stranger which door to knock on;
* a chat that cannot be resolved is never written to, and the attempt is audited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..decisions import DecisionOutcome, Responder, respond_to_decision
from ..messages import already_answered, answered, decision_body, expired, forbidden
from ..models import Decision, Delivery
from ..runtime import OutboundMessage, RunContext, resolve_ref

API = "https://api.telegram.org"
ALLOWED_UPDATES = ["callback_query"]
CALLBACK_LIMIT = 64  # Telegram's hard limit on callback_data, in bytes


class TelegramError(RuntimeError):
    pass


@dataclass
class Bot:
    """The thin HTTP client. Never logs the URL: it contains the token."""

    token: str
    timeout: float = 35.0
    _http: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=self.timeout)

    def call(self, method: str, **params: Any) -> Any:
        try:
            response = self._http.post(f"{API}/bot{self.token}/{method}", json=params)
            data = response.json()
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise TelegramError(f"{method}: non-JSON response") from exc
        if not data.get("ok"):
            raise TelegramError(f"{method} failed: {data.get('description')}")
        return data["result"]

    def close(self) -> None:
        self._http.close()


def callback_data(incident_id: str, decision_id: str, option_id: str) -> str:
    """`d|<incident>|<decision>|<option>`, kept inside Telegram's 64-byte cap.

    The payload carries no resident id and no chat id: what it identifies is a decision, and who
    may answer that decision is decided from the roster, not from anything a client sends back.
    The incident id is there because decision ids are only unique within an incident, and a
    webhook has to know which incident's coordinator to wake before it can look anything up.
    """
    data = f"d|{incident_id}|{decision_id}|{option_id}"
    if len(data.encode("utf-8")) > CALLBACK_LIMIT:
        raise ValueError(f"callback data too long for Telegram: {data!r}")
    return data


def parse_callback(data: str) -> tuple[str, str, str] | None:
    """`(incident_id, decision_id, option_id)`, or None for anything that is not ours."""
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != "d" or not all(parts[1:]):
        return None
    return parts[1], parts[2], parts[3]


def keyboard(decision: Decision) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": o.label,
                    "callback_data": callback_data(decision.incident_id, decision.id, o.id),
                }
            ]
            for o in decision.options
        ]
    }


async def process_tap(ctx: RunContext, query: dict[str, Any]) -> DecisionOutcome | None:
    """Turn one callback query into an answer, without touching Telegram.

    Used by the notifier below and, in the cloud, by a coordinator whose incident has no real
    channel (the restart test taps through the real webhook without messaging anyone).
    """
    parsed = parse_callback(str(query.get("data") or ""))
    if parsed is None:
        return None
    incident_id, decision_id, option_id = parsed
    if incident_id != ctx.incident_id:
        return DecisionOutcome("not_found", "That decision is not on this incident.")
    chat = str((query.get("message") or {}).get("chat", {}).get("id", ""))
    return await respond_to_decision(
        ctx, decision_id, option_id, Responder(source="telegram", external_id=chat)
    )


def chat_id_for(ctx: RunContext, member_id: str) -> str | None:
    try:
        member = ctx.store.volunteer(member_id)
    except Exception:  # noqa: BLE001 - an unknown id is simply not reachable
        return None
    return resolve_ref(member.telegram_chat_id_ref)


class TelegramNotifier:
    """Sends decisions and tasks, and turns taps into answers."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._offset = 0
        self._seen: set[int] = set()

    # --- outbound ---

    def deliver_decision(self, ctx: RunContext, decision: Decision) -> Delivery | None:
        chat = chat_id_for(ctx, decision.audience)
        if not chat:
            self._unreachable(ctx, decision.audience, decision.resident_id)
            return None
        sent = self.bot.call(
            "sendMessage",
            chat_id=chat,
            text=decision_body(ctx, decision),
            reply_markup=keyboard(decision),
        )
        delivery = Delivery(
            channel="telegram", recipient=decision.audience, message_ref=str(sent["message_id"])
        )
        decision.delivery.append(delivery)
        ctx.store.save_decision(decision)
        ctx.audit.record(
            actor="system:telegram",
            type="decision",
            resident_id=decision.resident_id,
            reason=f"{decision.id} sent to {decision.audience}",
            data={"decision_id": decision.id, "message_id": delivery.message_ref},
        )
        return delivery

    def deliver_message(self, ctx: RunContext, message: OutboundMessage) -> Delivery | None:
        """Send a message that needs no decision. Only roster members are reachable."""
        chat = chat_id_for(ctx, message.recipient)
        if not chat:
            self._unreachable(ctx, message.recipient, message.resident_id)
            return None
        sent = self.bot.call("sendMessage", chat_id=chat, text=message.text)
        ctx.audit.record(
            actor="system:telegram",
            type="note",
            resident_id=message.resident_id,
            reason=f"{message.kind} sent to {message.recipient}",
            data={"message_id": sent["message_id"]},
        )
        return Delivery(
            channel="telegram", recipient=message.recipient, message_ref=str(sent["message_id"])
        )

    def confirm(self, ctx: RunContext, decision: Decision, text: str) -> None:
        """Replace the message with the outcome, which also removes the buttons."""
        for delivery in decision.delivery:
            if delivery.channel != "telegram" or not delivery.message_ref:
                continue
            chat = chat_id_for(ctx, delivery.recipient)
            if not chat:
                continue
            body = decision_body(ctx, decision) + "\n\n" + text
            self.bot.call(
                "editMessageText",
                chat_id=chat,
                message_id=int(delivery.message_ref),
                text=body,
            )

    def _unreachable(self, ctx: RunContext, member_id: str, resident_id: str | None) -> None:
        ctx.audit.record(
            actor="system:telegram",
            type="policy",
            resident_id=resident_id,
            policy_decision="deny",
            reason=f"no chat id on file for {member_id}; nothing was sent",
            data={"by": "code"},
        )

    # --- inbound ---

    async def poll_once(self, ctx: RunContext, *, timeout: int = 0) -> list[str]:
        """Fetch taps and answer them. Returns one line per tap, for the board.

        An update already handled is skipped: Telegram redelivers until an offset is
        acknowledged, and a redelivered tap must not become a second answer.
        """
        lines: list[str] = []
        updates = self.bot.call(
            "getUpdates",
            offset=self._offset or None,
            timeout=timeout,
            allowed_updates=ALLOWED_UPDATES,
        )
        for update in updates:
            self._offset = max(self._offset, int(update["update_id"]) + 1)
            if update["update_id"] in self._seen:
                continue
            self._seen.add(update["update_id"])
            query = update.get("callback_query")
            if not query:
                continue
            line = await self.handle_tap(ctx, query)
            if line:
                lines.append(line)
        return lines

    async def handle_tap(self, ctx: RunContext, query: dict[str, Any]) -> str | None:
        """Answer one tap and show the human the result. Also the webhook's entry point."""
        outcome = await process_tap(ctx, query)
        if outcome is None:
            return None
        parsed = parse_callback(str(query.get("data") or ""))
        decision_id, option_id = (parsed[1], parsed[2]) if parsed else ("?", "?")

        toast, decision = outcome.message, outcome.decision
        if outcome.kind == "forbidden":
            toast = forbidden(ctx)
        self.bot.call("answerCallbackQuery", callback_query_id=query["id"], text=toast[:200])

        if decision is not None and outcome.kind in ("applied", "already_answered", "expired"):
            body = {
                "applied": answered,
                "already_answered": already_answered,
                "expired": expired,
            }[outcome.kind](decision)
            try:
                self.confirm(ctx, decision, body)
            except TelegramError:
                pass  # the toast already told them; a failed edit must not lose the answer
        who = (query.get("from") or {}).get("id", "?")
        # The incident's own clock, so a tap line and a decision line can be read side by side.
        at = ctx.clock.now().strftime("%H:%M:%S")
        return f"{at} tap {decision_id}/{option_id} from {who}: {outcome.kind}"
