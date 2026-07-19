"""Generate deterministic Freesolo SFT data for the intent parser.

The rows emitted here use Freesolo's documented task-record shape:
{"input": "...", "output": "..."}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

from intent.models import IntentCardContext
from intent.prompts import INTENT_PROMPT_VERSION, render_intent_prompt

GOALS = (
    "max_cashback",
    "max_travel",
    "credit_health",
    "hit_signup_bonus",
    "max_cashflow",
    "min_risk",
)

DEFAULT_CARDS = (
    IntentCardContext(id="maple-lite-cashback", name="Maple Lite Cashback", has_active_bonus=False),
    IntentCardContext(id="aeroplan-voyager", name="Aeroplan Voyager", has_active_bonus=True),
    IntentCardContext(id="cascade-cashback", name="Cascade Cashback", has_active_bonus=True),
)

REFERENCE_DATE = "2026-07-18"


def _intent(
    weights: dict[str, float],
    *,
    max_utilization_bps: int | None = None,
    max_utilization_until: str | None = None,
    bonus_cards: list[str] | None = None,
) -> dict[str, Any]:
    total = sum(max(0.0, float(weights.get(goal, 0.0))) for goal in GOALS)
    normalized = {
        goal: (max(0.0, float(weights.get(goal, 0.0))) / total if total > 0 else 0.0)
        for goal in GOALS
    }
    return {
        "weights": {goal: round(normalized[goal], 4) for goal in GOALS},
        "constraints": {
            "max_utilization_bps": max_utilization_bps,
            "max_utilization_until": max_utilization_until,
            "must_hit_bonus_card_ids": bonus_cards or [],
        },
    }


def _row(user_text: str, intent: dict[str, Any]) -> dict[str, str]:
    prompt = render_intent_prompt(
        user_text,
        reference_date=date.fromisoformat(REFERENCE_DATE),
        card_context=DEFAULT_CARDS,
    )
    return {
        "input": prompt,
        "output": json.dumps(intent, sort_keys=True, separators=(",", ":")),
    }


def _dominant_examples() -> list[dict[str, str]]:
    scenarios: list[tuple[str, dict[str, Any], list[str]]] = [
        (
            "cashback",
            _intent({"max_cashback": 0.75, "min_risk": 0.1, "max_cashflow": 0.15}),
            [
                "I mostly care about cash back on my bills.",
                "Maximize cash rewards. I do not care about points.",
                "Put my payments wherever the cash return is best.",
                "For rent and utilities I want the best cashback route.",
                "Cashback first, everything else second.",
                "I want the highest cash value back this month.",
            ],
        ),
        (
            "travel",
            _intent({"max_travel": 0.8, "max_cashback": 0.05, "min_risk": 0.15}),
            [
                "I am saving for a trip, so prioritize travel points.",
                "Use the card that earns the most miles.",
                "Travel rewards matter more than cash back right now.",
                "Route bills toward points for flights.",
                "I want airline points, not cashback.",
                "Optimize rent for travel value.",
            ],
        ),
        (
            "mortgage",
            _intent(
                {"credit_health": 0.7, "min_risk": 0.15, "max_cashflow": 0.15},
                max_utilization_bps=3000,
                max_utilization_until="2026-10-18",
            ),
            [
                "I am applying for a mortgage soon, keep utilization under 30%.",
                "Protect my credit score before a loan application.",
                "Keep balances low until October 18, 2026.",
                "Do not let any card go over 30 percent utilization.",
                "Mortgage coming up, credit health is the priority.",
                "I need lower utilization more than rewards.",
            ],
        ),
        (
            "bonus",
            _intent(
                {"hit_signup_bonus": 0.7, "max_travel": 0.15, "max_cashback": 0.15},
                bonus_cards=["aeroplan-voyager"],
            ),
            [
                "I need to finish the Aeroplan Voyager signup bonus.",
                "Make sure my payments help hit the welcome bonus.",
                "Prioritize the active bonus on Aeroplan Voyager.",
                "I want to hit my new card bonus before it expires.",
                "Use spend toward the travel card bonus first.",
                "The welcome offer matters most this cycle.",
            ],
        ),
        (
            "cashflow",
            _intent({"max_cashflow": 0.75, "min_risk": 0.15, "max_cashback": 0.1}),
            [
                "I need the longest float before cash leaves my account.",
                "Prioritize cash flow over rewards.",
                "Give me more time to pay these bills.",
                "I am tight on money, so maximize payment float.",
                "Cashflow matters most this month.",
                "Use the route that buys me the most time.",
            ],
        ),
        (
            "risk",
            _intent({"min_risk": 0.75, "credit_health": 0.15, "max_cashflow": 0.1}),
            [
                "Use the safest route with the most headroom.",
                "Avoid declined payments and capacity issues.",
                "Risk is my main concern, not rewards.",
                "Keep plenty of room on the card after payment.",
                "I want the least risky funding choice.",
                "Do not push any card close to its limit.",
            ],
        ),
    ]
    rows: list[dict[str, str]] = []
    for _, intent, texts in scenarios:
        rows.extend(_row(text, intent) for text in texts)
    return rows


def _mixed_examples(rng: random.Random) -> list[dict[str, str]]:
    templates = [
        (
            "I want {primary_phrase}, but also {secondary_phrase}.",
            {"primary": 0.6, "secondary": 0.3, "min_risk": 0.1},
        ),
        (
            "{primary_phrase} is the main thing; keep {secondary_phrase} in mind too.",
            {"primary": 0.65, "secondary": 0.25, "min_risk": 0.1},
        ),
        (
            "Balance {primary_phrase} with {secondary_phrase}.",
            {"primary": 0.5, "secondary": 0.35, "min_risk": 0.15},
        ),
        (
            "I care about {primary_phrase}, {secondary_phrase}, and not getting squeezed.",
            {"primary": 0.45, "secondary": 0.35, "min_risk": 0.2},
        ),
    ]
    phrases = {
        "max_cashback": ["cash back", "cash rewards", "getting money back"],
        "max_travel": ["travel points", "miles", "flight rewards"],
        "credit_health": ["low utilization", "my credit score", "credit health"],
        "hit_signup_bonus": ["my signup bonus", "the Aeroplan bonus", "welcome spend"],
        "max_cashflow": ["cash flow", "more payment float", "time before payoff"],
        "min_risk": ["payment safety", "available headroom", "avoiding declines"],
    }
    constraints = [
        {},
        {"max_utilization_bps": 3000, "max_utilization_until": "2026-10-18"},
        {"max_utilization_bps": 5000},
        {"bonus_cards": ["aeroplan-voyager"]},
        {"bonus_cards": ["cascade-cashback"]},
    ]

    rows: list[dict[str, str]] = []
    for primary in GOALS:
        for secondary in GOALS:
            if primary == secondary:
                continue
            for template, weights_shape in templates:
                weights = {goal: 0.0 for goal in GOALS}
                weights[primary] = weights_shape["primary"]
                weights[secondary] = weights_shape["secondary"]
                if primary != "min_risk" and secondary != "min_risk":
                    weights["min_risk"] = weights_shape["min_risk"]
                total = sum(weights.values())
                weights = {goal: value / total for goal, value in weights.items()}
                constraint = rng.choice(constraints)
                text = template.format(
                    primary_phrase=rng.choice(phrases[primary]),
                    secondary_phrase=rng.choice(phrases[secondary]),
                )
                if constraint.get("max_utilization_bps") == 3000:
                    text += " Keep utilization below 30% until October."
                elif constraint.get("max_utilization_bps") == 5000:
                    text += " Never cross 50% utilization."
                elif constraint.get("bonus_cards") == ["aeroplan-voyager"]:
                    text += " Also make sure Aeroplan Voyager bonus spend counts."
                    weights["hit_signup_bonus"] = max(weights["hit_signup_bonus"], 0.2)
                elif constraint.get("bonus_cards") == ["cascade-cashback"]:
                    text += " If possible, count spend toward Cascade Cashback."
                    weights["hit_signup_bonus"] = max(weights["hit_signup_bonus"], 0.2)
                total = sum(weights.values())
                weights = {goal: value / total for goal, value in weights.items()}
                rows.append(_row(text, _intent(weights, **constraint)))
    return rows


def _colloquial_examples(rng: random.Random) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    bases = [
        (
            "rent is scary this month, give me float but don't wreck my credit",
            {"max_cashflow": 0.45, "credit_health": 0.4, "min_risk": 0.15},
        ),
        (
            "i'm overdue on utilities, prioritize safe payment first then cashback",
            {"min_risk": 0.45, "max_cashback": 0.35, "max_cashflow": 0.2},
        ),
        (
            "new card bonus matters but don't tank utilization",
            {"hit_signup_bonus": 0.5, "credit_health": 0.35, "min_risk": 0.15},
        ),
        (
            "points points points, unless the card is too close to maxed out",
            {"max_travel": 0.65, "min_risk": 0.25, "credit_health": 0.1},
        ),
        (
            "just save me money on fees and get decent cashback",
            {"max_cashback": 0.5, "min_risk": 0.3, "max_cashflow": 0.2},
        ),
    ]
    modifiers = [
        "",
        " I need this for rent.",
        " Utilities are due too.",
        " This is for my recurring payments.",
        " Keep it practical.",
        " I do not need travel perks.",
    ]
    for text, weights in bases:
        for modifier in modifiers:
            constraint: dict[str, Any] = {}
            adjusted = dict(weights)
            final_text = text + modifier
            if rng.random() < 0.35:
                constraint["max_utilization_bps"] = 3000
                adjusted["credit_health"] = max(adjusted.get("credit_health", 0), 0.35)
                final_text += " Hard cap card usage at 30%."
            rows.append(_row(final_text, _intent(adjusted, **constraint)))
    return rows


def build_dataset(
    seed: int,
    target_train: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rng = random.Random(seed)
    rows = _dominant_examples() + _mixed_examples(rng) + _colloquial_examples(rng)
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        digest = hashlib.sha256(row["input"].encode("utf-8")).hexdigest()
        if digest not in seen:
            seen.add(digest)
            unique.append(row)
    rng.shuffle(unique)
    while len(unique) < target_train + 120:
        base = rng.choice(unique)
        unique.append({"input": base["input"], "output": base["output"]})
    split_at = min(target_train, int(len(unique) * 0.85))
    return unique[:split_at], unique[split_at:]


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("intent/training/freesolo/dataset"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-rows", type=int, default=240)
    args = parser.parse_args()

    train, eval_rows = build_dataset(args.seed, args.train_rows)
    train_hash = write_jsonl(args.out / "train.jsonl", train)
    eval_hash = write_jsonl(args.out / "eval.jsonl", eval_rows)
    manifest = {
        "schema": "freesolo-task-record-v1",
        "prompt_version": INTENT_PROMPT_VERSION,
        "seed": args.seed,
        "train_rows": len(train),
        "eval_rows": len(eval_rows),
        "train_sha256": train_hash,
        "eval_sha256": eval_hash,
    }
    (args.out.parent / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
