"""Versioned prompt and schema helpers for language-to-intent extraction."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from engine.models import Goal
from intent.models import IntentCardContext

INTENT_PROMPT_VERSION = "intent-v1"

INTENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["weights", "constraints"],
    "properties": {
        "weights": {
            "type": "object",
            "additionalProperties": False,
            "required": [goal.value for goal in Goal],
            "properties": {
                goal.value: {
                    "type": "number",
                    "description": "Finite nonnegative preference weight.",
                }
                for goal in Goal
            },
        },
        "constraints": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "max_utilization_bps": {
                    "type": ["integer", "null"],
                    "description": (
                        "Hard per-card utilization ceiling in basis points, "
                        "e.g. 30% is 3000."
                    ),
                },
                "max_utilization_until": {
                    "type": ["string", "null"],
                    "description": (
                        "Absolute ISO date through which the utilization ceiling applies."
                    ),
                },
                "must_hit_bonus_card_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Synthetic card IDs whose active signup bonus must be hit.",
                },
            },
        },
    },
}


def render_intent_prompt(
    text: str,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
) -> str:
    cards = [
        {
            "id": card.id,
            "name": card.name,
            "has_active_bonus": card.has_active_bonus,
        }
        for card in card_context
    ]
    return f"""
You convert a user's payment-routing preference into one JSON object.
Return JSON only. Do not recommend a card, calculate rewards, explain math, or add prose.

The output must contain:
- weights for exactly these goals: {", ".join(goal.value for goal in Goal)}
- constraints with max_utilization_bps, max_utilization_until, and must_hit_bonus_card_ids

Goal meanings:
- max_cashback: prefer cash rewards.
- max_travel: prefer travel points or miles value.
- credit_health: prefer lower utilization; mortgage or credit-application language should
  increase this.
- hit_signup_bonus: prefer completing an active welcome/spend bonus.
- max_cashflow: prefer longer payment float.
- min_risk: prefer available headroom and less capacity risk.

Rules:
- Weights must be finite and nonnegative. They may be any scale; the backend normalizes them.
- If the user does not care about a goal, use 0 for that goal.
- Convert utilization percentages to basis points. 30% means 3000.
- Use absolute ISO dates. Reference date is {reference_date.isoformat()}.
- Only use card IDs from the supplied synthetic card context.
- If no hard constraint is stated, set constraint fields to null or [].
- Preference language is a weight. Hard language like "never exceed 30%" is a constraint.

Synthetic card context:
{json.dumps(cards, ensure_ascii=True)}

User text:
{text}
""".strip()
