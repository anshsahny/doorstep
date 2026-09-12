"""What a channel has to do, and a recording one for drills and tests.

A channel renders a `Decision` and turns a tap into `respond_to_decision`. It holds no rules of
its own: not who may answer, not whether a decision is still open, not what a choice does. That
is what makes Phase 5's dashboard a second `Notifier` rather than a second implementation of
Doorstep's judgement.
"""

from __future__ import annotations

from typing import Protocol

from ..decisions import Responder
from ..models import Decision, Delivery
from ..runtime import OutboundMessage, RunContext


class Notifier(Protocol):
    """Delivers decisions and messages to humans, and reports what they answered."""

    def deliver_decision(self, ctx: RunContext, decision: Decision) -> Delivery | None:
        """Put a decision in front of the one person it is addressed to."""
        ...

    def deliver_message(self, ctx: RunContext, message: OutboundMessage) -> Delivery | None:
        """Send a message that needs no answer (a volunteer task, a tip, a broadcast)."""
        ...

    def confirm(self, ctx: RunContext, decision: Decision, text: str) -> None:
        """Tell the human what happened, replacing the buttons so they cannot be tapped again."""
        ...


class RecordingNotifier:
    """Delivers nothing; records what would have been sent.

    This is the default for drills and every test, so a run that is not explicitly asked to use
    a real channel cannot reach anyone's phone.
    """

    def __init__(self) -> None:
        self.decisions: list[Decision] = []
        self.messages: list[OutboundMessage] = []
        self.confirmations: list[tuple[str, str]] = []

    def deliver_decision(self, ctx: RunContext, decision: Decision) -> Delivery | None:
        self.decisions.append(decision)
        return Delivery(channel="board", recipient=decision.audience)

    def deliver_message(self, ctx: RunContext, message: OutboundMessage) -> Delivery | None:
        self.messages.append(message)
        return Delivery(channel="board", recipient=message.recipient)

    def confirm(self, ctx: RunContext, decision: Decision, text: str) -> None:
        self.confirmations.append((decision.id, text))


def responder_for(source: str, external_id: str) -> Responder:
    return Responder(source=source, external_id=external_id)  # type: ignore[arg-type]
