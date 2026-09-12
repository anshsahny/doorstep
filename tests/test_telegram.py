"""The Telegram channel, with a fake Bot: no network, no token, nobody's phone.

What is being checked is that the channel only renders and routes. Every rule it appears to
enforce — who may answer, whether this was already answered — is `respond_to_decision`'s, and
these tests make sure the channel neither duplicates nor bypasses it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import make_ctx
from doorstep_agent.decisions import upsert_decision
from doorstep_agent.models import CaseState, CheckinResult, CheckinStatus, DecisionOption
from doorstep_agent.notify.telegram import (
    CALLBACK_LIMIT,
    TelegramNotifier,
    callback_data,
    keyboard,
    parse_callback,
)
from doorstep_agent.runtime import RunContext

CAPTAIN_CHAT = "42424242"
TOM_CHAT = "51515151"
STRANGER_CHAT = "99999999"


class FakeBot:
    """Records calls and returns what Telegram would."""

    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates = updates or []
        self._message_id = 100

    def call(self, method: str, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return batch
        if method == "sendMessage":
            self._message_id += 1
            return {"message_id": self._message_id}
        return True

    def sent_to(self, chat: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == "sendMessage" and str(p["chat_id"]) == chat]

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


@pytest.fixture(autouse=True)
def chat_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CAPTAIN_CHAT_ID", CAPTAIN_CHAT)
    monkeypatch.setenv("TELEGRAM_VOLUNTEER_CHAT_IDS", TOM_CHAT)


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    return make_ctx(auto_approve=False, sessions_dir=tmp_path / "sessions")


def a_pending_decision(ctx: RunContext, resident_id: str = "r01"):
    case = ctx.store.case(ctx.incident_id, resident_id)
    ctx.policy.start_attempt(case, "simulated")
    ctx.policy.apply_result(case, CheckinResult(status=CheckinStatus.URGENT, key_quote="dizzy"))
    from doorstep_agent.state_machine import transition

    transition(case, CaseState.ESCALATED, reason="escalated")
    ctx.store.save_case(case)

    decision = upsert_decision(
        ctx,
        tool_use_id=f"tu-{resident_id}",
        resident_id=resident_id,
        name="doorstep-urgent-red-flag",
        reason="Said she is dizzy.",
        options=[
            DecisionOption(
                id="handle",
                label="I'm handling it",
                action="resolve",
                args={"resident_id": resident_id, "outcome": "captain handling"},
            ),
            DecisionOption(id="send", label="Send Tom (0.4 km)", action="acknowledge"),
        ],
        audience="cap-maria",
    )
    decision.status = "pending"
    decision.expires_at = ctx.clock.after(15)
    ctx.store.save_decision(decision)
    return decision


def tap(decision_id: str, option_id: str, chat: str, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": int(chat)},
            "message": {"message_id": 101, "chat": {"id": int(chat)}},
            "data": f"d|{decision_id}|{option_id}",
        },
    }


# --- the callback payload ----------------------------------------------------------------


def test_callback_data_round_trips_and_fits_telegrams_limit() -> None:
    data = callback_data("dec-001", "send_volunteer")
    assert len(data.encode("utf-8")) <= CALLBACK_LIMIT
    assert parse_callback(data) == ("dec-001", "send_volunteer")


def test_callback_data_refuses_to_silently_truncate() -> None:
    with pytest.raises(ValueError, match="too long"):
        callback_data("dec-" + "x" * 60, "approve")


def test_rubbish_callbacks_are_ignored() -> None:
    assert parse_callback("") is None
    assert parse_callback("hello") is None
    assert parse_callback("x|dec-001|approve") is None


def test_every_option_becomes_one_button(ctx: RunContext) -> None:
    decision = a_pending_decision(ctx)
    rows = keyboard(decision)["inline_keyboard"]
    assert [row[0]["text"] for row in rows] == [o.label for o in decision.options]
    assert all(parse_callback(row[0]["callback_data"]) for row in rows)


# --- sending -----------------------------------------------------------------------------


def test_a_decision_goes_to_the_captains_chat_only(ctx: RunContext) -> None:
    bot = FakeBot()
    decision = a_pending_decision(ctx)

    delivery = TelegramNotifier(bot).deliver_decision(ctx, decision)

    assert delivery is not None and delivery.recipient == "cap-maria"
    assert len(bot.sent_to(CAPTAIN_CHAT)) == 1
    assert bot.sent_to(TOM_CHAT) == [] and bot.sent_to(STRANGER_CHAT) == []
    body = bot.sent_to(CAPTAIN_CHAT)[0]["text"]
    assert "Rose" in body and "fictional drill data" in body
    # The delivery is recorded on the decision, so the message can be edited when answered.
    assert ctx.store.decision(decision.id).delivery[0].message_ref == delivery.message_ref


def test_a_member_with_no_chat_id_is_never_messaged(ctx: RunContext, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_CAPTAIN_CHAT_ID", raising=False)
    bot = FakeBot()
    decision = a_pending_decision(ctx)

    assert TelegramNotifier(bot).deliver_decision(ctx, decision) is None
    assert bot.methods() == [], "nothing may be sent to a chat we cannot resolve"
    denials = [e for e in ctx.audit.events() if e.policy_decision == "deny"]
    assert denials and "no chat id on file" in denials[-1].reason


# --- taps --------------------------------------------------------------------------------


async def test_a_captains_tap_is_applied_and_the_message_is_edited(ctx: RunContext) -> None:
    decision = a_pending_decision(ctx)
    bot = FakeBot()
    notifier = TelegramNotifier(bot)
    notifier.deliver_decision(ctx, decision)
    bot.updates = [tap(decision.id, "handle", CAPTAIN_CHAT)]

    lines = await notifier.poll_once(ctx)

    assert lines and "applied" in lines[0]
    assert ctx.store.decision(decision.id).responder == "captain:cap-maria"
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.RESOLVED
    assert "answerCallbackQuery" in bot.methods()
    # Editing the message is what removes the buttons, so they cannot be tapped again.
    assert "editMessageText" in bot.methods()


async def test_a_strangers_tap_changes_nothing(ctx: RunContext) -> None:
    decision = a_pending_decision(ctx)
    bot = FakeBot()
    notifier = TelegramNotifier(bot)
    notifier.deliver_decision(ctx, decision)
    bot.updates = [tap(decision.id, "handle", STRANGER_CHAT)]

    lines = await notifier.poll_once(ctx)

    assert lines and "forbidden" in lines[0]
    assert ctx.store.decision(decision.id).status == "pending"
    assert ctx.store.case(ctx.incident_id, "r01").state == CaseState.ESCALATED
    toast = [p for m, p in bot.calls if m == "answerCallbackQuery"][-1]
    assert "not on the" in toast["text"]
    assert "editMessageText" not in bot.methods(), "a refused tap must not rewrite the message"


async def test_a_redelivered_update_is_not_answered_twice(ctx: RunContext) -> None:
    """Telegram redelivers until an offset is acknowledged; that must not become two answers."""
    decision = a_pending_decision(ctx)
    bot = FakeBot()
    notifier = TelegramNotifier(bot)
    notifier.deliver_decision(ctx, decision)

    bot.updates = [tap(decision.id, "handle", CAPTAIN_CHAT, update_id=7)]
    first = await notifier.poll_once(ctx)
    bot.updates = [tap(decision.id, "handle", CAPTAIN_CHAT, update_id=7)]
    second = await notifier.poll_once(ctx)

    assert "applied" in first[0]
    assert second == [], "the same update id must be skipped outright"


async def test_tapping_twice_for_real_is_reported_not_replayed(ctx: RunContext) -> None:
    """Two genuine taps, two update ids: the second is recognised, and says so."""
    decision = a_pending_decision(ctx)
    bot = FakeBot()
    notifier = TelegramNotifier(bot)
    notifier.deliver_decision(ctx, decision)

    bot.updates = [tap(decision.id, "handle", CAPTAIN_CHAT, update_id=1)]
    first = await notifier.poll_once(ctx)
    bot.updates = [tap(decision.id, "handle", CAPTAIN_CHAT, update_id=2)]
    second = await notifier.poll_once(ctx)

    assert "applied" in first[0]
    assert "already_answered" in second[0]
    toast = [p for m, p in bot.calls if m == "answerCallbackQuery"][-1]
    assert "Nothing was sent twice" in toast["text"]
    resolutions = [
        t
        for t in ctx.store.case(ctx.incident_id, "r01").history
        if t.to_state == CaseState.RESOLVED
    ]
    assert len(resolutions) == 1


async def test_the_poll_advances_its_offset(ctx: RunContext) -> None:
    decision = a_pending_decision(ctx)
    bot = FakeBot([tap(decision.id, "handle", CAPTAIN_CHAT, update_id=11)])
    notifier = TelegramNotifier(bot)

    await notifier.poll_once(ctx)
    await notifier.poll_once(ctx)

    offsets = [p.get("offset") for m, p in bot.calls if m == "getUpdates"]
    assert offsets == [None, 12], "an acknowledged update must not be fetched forever"
