"""Safe language-model card context derived from validated engine cards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from engine.models import Card
from intent.models import IntentCardContext


def build_intent_card_context(
    cards: Sequence[Card],
    reference_date: date,
) -> tuple[IntentCardContext, ...]:
    if len(cards) != len({card.id for card in cards}):
        raise ValueError("card IDs must be unique")
    return tuple(
        IntentCardContext(
            id=card.id,
            name=card.name,
            has_active_bonus=(
                card.signup_bonus is not None
                and card.signup_bonus.remaining_spend_cents > 0
                and card.signup_bonus.deadline_date >= reference_date
            ),
        )
        for card in sorted(cards, key=lambda card: card.id)
    )