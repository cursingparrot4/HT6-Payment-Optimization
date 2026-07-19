from __future__ import annotations

import json
from datetime import date

from engine.models import Goal
from engine.objective import quantize_intent_weights
from intent.models import IntentCardContext, SamplingRegime
from intent.parser import parse_provider_output
from intent.sampling import (
    SamplingConfig,
    latent_target_intent,
    latent_target_json,
    sample_latent_intents,
    split_latents,
)


def contexts() -> tuple[IntentCardContext, ...]:
    return (
        IntentCardContext(id="bonus-card", name="Bonus Card", has_active_bonus=True),
        IntentCardContext(id="plain-card", name="Plain Card", has_active_bonus=False),
    )


def test_sampling_is_seeded_and_every_ppm_vector_is_exact() -> None:
    config = SamplingConfig(seed=42, latent_count=80, test_fraction_bps=1_500)
    first = sample_latent_intents(config, date(2026, 7, 18), contexts())
    second = sample_latent_intents(config, date(2026, 7, 18), contexts())

    assert [latent.model_dump() for latent in first] == [
        latent.model_dump() for latent in second
    ]
    assert all(sum(latent.weights_ppm.values()) == 1_000_000 for latent in first)
    assert all(list(latent.weights_ppm) == list(Goal) for latent in first)
    assert all(1 <= len(latent.styles) <= 3 for latent in first)


def test_sampling_regimes_and_constraints_hold_their_contracts() -> None:
    latents = sample_latent_intents(
        SamplingConfig(seed=7, latent_count=200),
        date(2026, 7, 18),
        contexts(),
    )

    assert set(latent.regime for latent in latents) == set(SamplingRegime)
    sparse = [latent for latent in latents if latent.regime is SamplingRegime.SPARSE]
    two_goal = [latent for latent in latents if latent.regime is SamplingRegime.TWO_GOAL]
    constraint_heavy = [
        latent for latent in latents if latent.regime is SamplingRegime.CONSTRAINT_HEAVY
    ]
    assert all(max(latent.weights_ppm.values()) >= 650_000 for latent in sparse)
    assert all(
        sum(sorted(latent.weights_ppm.values(), reverse=True)[:2]) >= 800_000
        for latent in two_goal
    )
    assert all(
        latent.constraints.max_utilization_bps is not None
        or latent.constraints.must_hit_bonus_card_ids
        for latent in constraint_heavy
    )
    assert all(
        set(latent.constraints.must_hit_bonus_card_ids) <= {"bonus-card"}
        for latent in latents
    )


def test_split_occurs_by_latent_id_and_is_reproducible() -> None:
    config = SamplingConfig(seed=42, latent_count=40, test_fraction_bps=1_500)
    latents = sample_latent_intents(config, date(2026, 7, 18), contexts())
    train, test = split_latents(
        latents,
        seed=config.seed,
        test_fraction_bps=config.test_fraction_bps,
    )
    train_again, test_again = split_latents(
        latents,
        seed=config.seed,
        test_fraction_bps=config.test_fraction_bps,
    )

    assert len(train) == 34
    assert len(test) == 6
    assert {latent.latent_id for latent in train}.isdisjoint(
        latent.latent_id for latent in test
    )
    assert [latent.latent_id for latent in train] == [
        latent.latent_id for latent in train_again
    ]
    assert [latent.latent_id for latent in test] == [
        latent.latent_id for latent in test_again
    ]


def test_target_json_round_trips_to_the_same_canonical_ppm() -> None:
    latent = sample_latent_intents(
        SamplingConfig(seed=11, latent_count=2),
        date(2026, 7, 18),
        contexts(),
    )[0]
    raw = latent_target_json(latent)
    parsed_json = json.loads(raw)
    parsed = parse_provider_output(raw, contexts())

    assert set(parsed_json) == {"weights", "constraints"}
    assert quantize_intent_weights(parsed.intent) == latent.weights_ppm
    assert quantize_intent_weights(latent_target_intent(latent)) == latent.weights_ppm


def test_sampling_without_bonus_cards_never_invents_forced_bonus_ids() -> None:
    no_bonus_context = (
        IntentCardContext(id="plain", name="Plain Card", has_active_bonus=False),
    )
    latents = sample_latent_intents(
        SamplingConfig(seed=99, latent_count=50),
        date(2026, 7, 18),
        no_bonus_context,
    )

    assert all(not latent.constraints.must_hit_bonus_card_ids for latent in latents)
