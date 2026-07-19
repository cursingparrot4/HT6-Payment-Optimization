"""Freesolo single-turn environment for SwitchPay intent parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from freesolo.datasets import TaskExample
from freesolo.datasets.records import load_task_examples
from freesolo.environments import EnvironmentSingleTurn, RewardResult

ROOT = Path(__file__).parent
GOALS = {
    "max_cashback",
    "max_travel",
    "credit_health",
    "hit_signup_bonus",
    "max_cashflow",
    "min_risk",
}
CONSTRAINTS = {
    "max_utilization_bps",
    "max_utilization_until",
    "must_hit_bonus_card_ids",
}


class SwitchPayIntentEnv(EnvironmentSingleTurn):
    def __init__(self, *, split: str = "train") -> None:
        self.dataset = load_task_examples(ROOT / "dataset" / f"{split}.jsonl")

    def build_prompt_messages(self, example: TaskExample, prompt_text: str):
        del prompt_text
        return [{"role": "user", "content": example.input}]

    def score_response(self, example: TaskExample, response_text: str) -> RewardResult:
        try:
            expected = _load_intent(str(example.output or ""))
            actual = _load_intent(str(response_text))
        except ValueError as exc:
            return RewardResult(score=0.0, threshold=0.98, metadata={"error": str(exc)[:120]})

        weight_error = sum(
            abs(expected["weights"][goal] - actual["weights"][goal]) for goal in GOALS
        ) / len(GOALS)
        constraint_score = 1.0 if expected["constraints"] == actual["constraints"] else 0.0
        score = max(0.0, 1.0 - weight_error) * 0.75 + constraint_score * 0.25
        return RewardResult(score=score, threshold=0.98)


def load_environment(split: str = "train", **kwargs) -> SwitchPayIntentEnv:
    del kwargs
    return SwitchPayIntentEnv(split=split)


def _load_intent(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped:
        raise ValueError("empty response")
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("root must be an object")
    if set(payload) != {"weights", "constraints"}:
        raise ValueError("root keys must be weights and constraints")

    weights = payload["weights"]
    if not isinstance(weights, dict) or set(weights) != GOALS:
        raise ValueError("weights must contain exactly six goals")
    parsed_weights: dict[str, float] = {}
    for goal, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise ValueError(f"invalid weight for {goal}")
        parsed_weights[goal] = float(value)
    total = sum(parsed_weights.values())
    if total <= 0:
        raise ValueError("weights must have positive mass")
    parsed_weights = {goal: value / total for goal, value in parsed_weights.items()}

    constraints = payload["constraints"]
    if not isinstance(constraints, dict) or set(constraints) != CONSTRAINTS:
        raise ValueError("constraints have invalid keys")
    util = constraints["max_utilization_bps"]
    if util is not None and (
        isinstance(util, bool) or not isinstance(util, int) or util < 0 or util > 10000
    ):
        raise ValueError("invalid utilization cap")
    until = constraints["max_utilization_until"]
    if until is not None and not isinstance(until, str):
        raise ValueError("invalid utilization date")
    bonus_ids = constraints["must_hit_bonus_card_ids"]
    if not isinstance(bonus_ids, list) or not all(isinstance(item, str) for item in bonus_ids):
        raise ValueError("invalid bonus ids")

    return {
        "weights": parsed_weights,
        "constraints": {
            "max_utilization_bps": util,
            "max_utilization_until": until,
            "must_hit_bonus_card_ids": sorted(bonus_ids),
        },
    }
