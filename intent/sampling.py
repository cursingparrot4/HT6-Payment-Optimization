"""Seeded latent intent and constraint sampling for reverse data generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

import numpy as np

from engine.models import Constraint, Goal, Intent
from intent.models import (
    IntentCardContext,
    LanguageStyle,
    LatentIntent,
    SamplingRegime,
)


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    seed: int = 42
    latent_count: int = 1_000
    test_fraction_bps: int = 1_500

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if (
            isinstance(self.latent_count, bool)
            or not isinstance(self.latent_count, int)
            or self.latent_count < 2
        ):
            raise ValueError("latent_count must be an integer of at least two")
        if (
            isinstance(self.test_fraction_bps, bool)
            or not isinstance(self.test_fraction_bps, int)
            or not 1 <= self.test_fraction_bps <= 9_999
        ):
            raise ValueError("test_fraction_bps must be an integer from 1 through 9,999")


def _quantize(values: list[float], scale: int = 1_000_000) -> dict[Goal, int]:
    decimals = [Decimal(str(value)) for value in values]
    total = sum(decimals, start=Decimal(0))
    if total <= 0:
        raise ValueError("sampled weights must contain a positive value")
    exact = [value * scale / total for value in decimals]
    floors = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remaining = scale - sum(floors)
    order = sorted(
        range(len(exact)),
        key=lambda index: (-(exact[index] - floors[index]), index),
    )
    for index in order[:remaining]:
        floors[index] += 1
    return {goal: floors[index] for index, goal in enumerate(Goal)}


def _sample_regime(rng: np.random.Generator) -> SamplingRegime:
    value = float(rng.random())
    if value < 0.40:
        return SamplingRegime.BALANCED
    if value < 0.70:
        return SamplingRegime.SPARSE
    if value < 0.90:
        return SamplingRegime.TWO_GOAL
    return SamplingRegime.CONSTRAINT_HEAVY


def _sample_weights(
    rng: np.random.Generator,
    regime: SamplingRegime,
) -> dict[Goal, int]:
    goal_count = len(Goal)
    if regime is SamplingRegime.BALANCED:
        values = rng.dirichlet(np.full(goal_count, 1.5)).tolist()
    elif regime is SamplingRegime.SPARSE:
        dominant = int(rng.integers(goal_count))
        dominant_share = float(rng.uniform(0.65, 0.90))
        remainder = rng.dirichlet(np.full(goal_count - 1, 0.3)) * (1 - dominant_share)
        values = []
        remainder_index = 0
        for index in range(goal_count):
            if index == dominant:
                values.append(dominant_share)
            else:
                values.append(float(remainder[remainder_index]))
                remainder_index += 1
    elif regime is SamplingRegime.TWO_GOAL:
        selected = sorted(int(index) for index in rng.choice(goal_count, size=2, replace=False))
        combined = float(rng.uniform(0.80, 0.98))
        first_share = float(rng.uniform(0.25, 0.75)) * combined
        second_share = combined - first_share
        remainder = rng.dirichlet(np.full(goal_count - 2, 0.5)) * (1 - combined)
        values = []
        remainder_index = 0
        for index in range(goal_count):
            if index == selected[0]:
                values.append(first_share)
            elif index == selected[1]:
                values.append(second_share)
            else:
                values.append(float(remainder[remainder_index]))
                remainder_index += 1
    else:
        values = rng.dirichlet(np.full(goal_count, 2.0)).tolist()
    return _quantize(values)


def _sample_constraints(
    rng: np.random.Generator,
    regime: SamplingRegime,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
) -> Constraint:
    active_bonus_ids = [card.id for card in card_context if card.has_active_bonus]
    if regime is not SamplingRegime.CONSTRAINT_HEAVY and float(rng.random()) < 0.50:
        return Constraint()

    use_utilization = regime is SamplingRegime.CONSTRAINT_HEAVY or float(rng.random()) < 0.75
    use_bonus = bool(active_bonus_ids) and (
        regime is SamplingRegime.CONSTRAINT_HEAVY or float(rng.random()) < 0.40
    )
    if not use_utilization and not use_bonus:
        use_utilization = True

    ceiling: int | None = None
    cutoff: date | None = None
    if use_utilization:
        ceiling = int(rng.choice([2_000, 2_500, 3_000, 4_000]))
        if regime is SamplingRegime.CONSTRAINT_HEAVY or float(rng.random()) < 0.65:
            cutoff = reference_date + timedelta(days=int(rng.integers(30, 181)))
    forced: list[str] = []
    if use_bonus:
        forced = [str(rng.choice(active_bonus_ids))]
    return Constraint(
        max_utilization_bps=ceiling,
        max_utilization_until=cutoff,
        must_hit_bonus_card_ids=forced,
    )


def _sample_styles(rng: np.random.Generator) -> tuple[LanguageStyle, ...]:
    styles = list(LanguageStyle)
    count = int(rng.integers(1, 4))
    selected = rng.choice(len(styles), size=count, replace=False)
    return tuple(styles[int(index)] for index in selected)


def sample_latent_intents(
    config: SamplingConfig,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
) -> list[LatentIntent]:
    if len(card_context) != len({card.id for card in card_context}):
        raise ValueError("card context IDs must be unique")
    canonical_context = tuple(sorted(card_context, key=lambda card: card.id))
    rng = np.random.default_rng(config.seed)
    latents = []
    for index in range(config.latent_count):
        regime = _sample_regime(rng)
        latents.append(
            LatentIntent(
                latent_id=f"latent-{index:06d}",
                reference_date=reference_date,
                weights_ppm=_sample_weights(rng, regime),
                constraints=_sample_constraints(
                    rng,
                    regime,
                    reference_date,
                    canonical_context,
                ),
                card_context=canonical_context,
                regime=regime,
                styles=_sample_styles(rng),
            )
        )
    return latents


def split_latents(
    latents: list[LatentIntent],
    *,
    seed: int,
    test_fraction_bps: int,
) -> tuple[list[LatentIntent], list[LatentIntent]]:
    if len(latents) < 2:
        raise ValueError("at least two latent intents are required for a split")
    if len(latents) != len({latent.latent_id for latent in latents}):
        raise ValueError("latent IDs must be unique")
    rng = np.random.default_rng(seed + 1)
    shuffled = list(rng.permutation(len(latents)))
    test_count = max(1, len(latents) * test_fraction_bps // 10_000)
    test_indices = set(shuffled[:test_count])
    train = sorted(
        (latent for index, latent in enumerate(latents) if index not in test_indices),
        key=lambda latent: latent.latent_id,
    )
    test = sorted(
        (latent for index, latent in enumerate(latents) if index in test_indices),
        key=lambda latent: latent.latent_id,
    )
    return train, test


def latent_target_payload(latent: LatentIntent) -> dict[str, object]:
    return {
        "weights": {
            goal.value: latent.weights_ppm[goal] / 1_000_000 for goal in Goal
        },
        "constraints": latent.constraints.model_dump(mode="json"),
    }


def latent_target_json(latent: LatentIntent) -> str:
    return json.dumps(
        latent_target_payload(latent),
        ensure_ascii=True,
        separators=(",", ":"),
    )


def latent_target_intent(latent: LatentIntent) -> Intent:
    return Intent.model_validate(latent_target_payload(latent))
