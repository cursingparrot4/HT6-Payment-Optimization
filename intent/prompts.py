"""Versioned prompt construction for strict intent JSON generation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

from engine.models import Goal
from intent.models import IntentCardContext

INTENT_PROMPT_VERSION = "intent-v1"


def build_intent_system_prompt(
    reference_date: date,
    card_context: Sequence[IntentCardContext],
) -> str:
    cards = [
        {
            "id": card.id,
            "name": card.name,
            "has_active_bonus": card.has_active_bonus,
        }
        for card in sorted(card_context, key=lambda item: item.id)
    ]
    goals = [goal.value for goal in Goal]
    return "\n".join(
        [
            "You convert a user's payment goals into one strict JSON object.",
            "Return JSON only: no Markdown fence, prose, recommendation, or money calculation.",
            f"Prompt contract version: {INTENT_PROMPT_VERSION}.",
            f"Reference date: {reference_date.isoformat()}.",
            f"Required weight keys: {json.dumps(goals, separators=(',', ':'))}.",
            "Every weight must be finite and nonnegative; include every key and make the sum 1.",
            "A preference such as 'keep utilization low' raises credit_health weight.",
            "Only explicit hard language such as 'never exceed 30%' creates "
            "max_utilization_bps=3000.",
            "If a utilization cutoff is requested, output an absolute ISO date in "
            "max_utilization_until.",
            "must_hit_bonus_card_ids may contain only listed card IDs whose "
            "has_active_bonus is true.",
            "Unspecified constraints are null or an empty list.",
            f"Allowed card context: {json.dumps(cards, separators=(',', ':'), sort_keys=True)}.",
            "Required shape: "
            '{"weights":{"max_cashback":0,"max_travel":0,"credit_health":0,'
            '"hit_signup_bonus":0,"max_cashflow":0,"min_risk":0},'
            '"constraints":{"max_utilization_bps":null,'
            '"max_utilization_until":null,"must_hit_bonus_card_ids":[]}}',
        ]
    )
