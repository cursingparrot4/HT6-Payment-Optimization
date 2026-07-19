"""Pure metric calculations for the eval harness.

Every function here is deterministic arithmetic over already-computed inputs; no
model, engine, or network calls. Denominator rules follow IMPLEMENTATION.md §6:
invalid outputs are evidence, so they stay in every end-to-end denominator — a
schema-invalid parse counts as a mismatch on every probe, zero monthly agreement,
and all-gold-atoms-missed on constraints. Weight error uses integer ppm so results
reproduce exactly across platforms.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from engine.models import Constraint, Goal, Intent
from engine.objective import quantize_intent_weights
from eval.models import (
    BootstrapInterval,
    ConstraintFieldMetrics,
    ParseOutcome,
)


def json_parse_rate(outcomes: Sequence[ParseOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if o.json_parse_ok) / len(outcomes)


def schema_valid_rate(outcomes: Sequence[ParseOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if o.schema_valid) / len(outcomes)


def weight_mae_ppm(
    pairs: Sequence[tuple[Intent, Intent]],
) -> tuple[int | None, dict[Goal, int], int]:
    """Macro MAE in integer ppm over (gold, predicted) pairs for valid predictions.

    Returns (overall_mae_ppm, per_goal_mae_ppm, valid_count). Overall is the mean
    absolute ppm difference across all six goals and all valid examples; per-goal is
    the mean absolute difference for that goal. ``None`` when nothing was valid —
    an MAE without coverage would be meaningless (§8).
    """

    if not pairs:
        return None, {}, 0
    goal_totals: dict[Goal, int] = {goal: 0 for goal in Goal}
    for gold, predicted in pairs:
        gold_ppm = quantize_intent_weights(gold)
        predicted_ppm = quantize_intent_weights(predicted)
        for goal in Goal:
            goal_totals[goal] += abs(predicted_ppm[goal] - gold_ppm[goal])
    count = len(pairs)
    per_goal = {goal: round(goal_totals[goal] / count) for goal in Goal}
    overall = round(sum(goal_totals.values()) / (6 * count))
    return overall, per_goal, count


def constraint_atoms(constraint: Constraint) -> frozenset[str]:
    """Flatten a constraint into atomic facts for micro precision/recall (§9)."""

    atoms: set[str] = set()
    if constraint.max_utilization_bps is not None:
        atoms.add(f"max_utilization_bps={constraint.max_utilization_bps}")
    if constraint.max_utilization_until is not None:
        atoms.add(f"max_utilization_until={constraint.max_utilization_until.isoformat()}")
    for card_id in constraint.must_hit_bonus_card_ids:
        atoms.add(f"must_hit_bonus_card_id={card_id}")
    return frozenset(atoms)


def constraint_metrics(
    rows: Sequence[tuple[Constraint, Constraint | None]],
) -> ConstraintFieldMetrics:
    """Exact-match and atom micro counts over (gold, predicted-or-invalid) rows.

    ``None`` prediction means the output was invalid: every field counts as
    incorrect and every gold atom becomes a false negative (§6/§9).
    """

    bps_exact = until_exact = bonus_exact = whole_exact = 0
    tp = fp = fn = 0
    for gold, predicted in rows:
        gold_atoms = constraint_atoms(gold)
        if predicted is None:
            fn += len(gold_atoms)
            continue
        bps_match = predicted.max_utilization_bps == gold.max_utilization_bps
        until_match = predicted.max_utilization_until == gold.max_utilization_until
        bonus_match = set(predicted.must_hit_bonus_card_ids) == set(gold.must_hit_bonus_card_ids)
        bps_exact += bps_match
        until_exact += until_match
        bonus_exact += bonus_match
        whole_exact += bps_match and until_match and bonus_match
        predicted_atoms = constraint_atoms(predicted)
        tp += len(gold_atoms & predicted_atoms)
        fp += len(predicted_atoms - gold_atoms)
        fn += len(gold_atoms - predicted_atoms)
    return ConstraintFieldMetrics(
        max_utilization_bps_exact=bps_exact,
        max_utilization_until_exact=until_exact,
        must_hit_bonus_exact=bonus_exact,
        whole_constraint_exact=whole_exact,
        denominator=len(rows),
        atom_true_positives=tp,
        atom_false_positives=fp,
        atom_false_negatives=fn,
    )


def downstream_match_rate(matches: Mapping[str, Sequence[bool]]) -> float:
    """Fraction of (example, probe) cells where predicted and gold winners agree."""

    total = sum(len(cells) for cells in matches.values())
    if total == 0:
        return 0.0
    return sum(sum(cells) for cells in matches.values()) / total


def cluster_bootstrap_interval(
    matches: Mapping[str, Sequence[bool]],
    *,
    seed: int,
    resamples: int,
) -> BootstrapInterval:
    """Percentile interval from a deterministic bootstrap over example IDs (§11).

    Resampling whole examples keeps each example's probe outcomes together, so
    correlated probes are never treated as independent draws.
    """

    example_ids = sorted(matches)
    if not example_ids or resamples < 1:
        return BootstrapInterval(seed=seed, resamples=resamples, lower=0.0, upper=0.0)
    rng = random.Random(seed)
    rates: list[float] = []
    for _ in range(resamples):
        sampled = [example_ids[rng.randrange(len(example_ids))] for _ in example_ids]
        hits = sum(sum(matches[example_id]) for example_id in sampled)
        cells = sum(len(matches[example_id]) for example_id in sampled)
        rates.append(hits / cells if cells else 0.0)
    rates.sort()

    def percentile(fraction: float) -> float:
        # Nearest-rank on the sorted resample distribution: deterministic and simple.
        index = min(len(rates) - 1, max(0, round(fraction * (len(rates) - 1))))
        return rates[index]

    return BootstrapInterval(
        seed=seed,
        resamples=resamples,
        lower=percentile(0.025),
        upper=percentile(0.975),
    )


def monthly_agreement(
    agreements: Sequence[float | None],
) -> tuple[float | None, float | None, int]:
    """(mean agreement, exact-plan match rate, unavailable count) per §12.

    ``None`` entries are unavailable solves (either plan not exactly optimal); they
    are excluded from the mean but counted, never silently compared. Invalid parses
    must be passed as 0.0 by the caller — they are zero agreement, not unavailable.
    """

    unavailable = sum(1 for value in agreements if value is None)
    available = [value for value in agreements if value is not None]
    if not available:
        return None, None, unavailable
    mean = sum(available) / len(available)
    exact = sum(1 for value in available if value == 1.0) / len(available)
    return mean, exact, unavailable
