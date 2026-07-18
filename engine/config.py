"""Frozen calibration and solver configuration for the deterministic engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


def _require_int(
    name: str,
    value: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")


@dataclass(frozen=True, slots=True)
class UtilizationBand:
    """One upper boundary and incremental slope in a convex penalty curve."""

    upper_bps: int
    penalty_points_per_bps: int

    def __post_init__(self) -> None:
        _require_int("upper_bps", self.upper_bps, minimum=1, maximum=10_000)
        _require_int(
            "penalty_points_per_bps",
            self.penalty_points_per_bps,
            minimum=0,
        )


DEFAULT_UTILIZATION_BANDS = (
    UtilizationBand(upper_bps=3_000, penalty_points_per_bps=0),
    UtilizationBand(upper_bps=5_000, penalty_points_per_bps=2),
    UtilizationBand(upper_bps=7_500, penalty_points_per_bps=6),
    UtilizationBand(upper_bps=10_000, penalty_points_per_bps=20),
)


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Versioned assumptions shared by pure scoring, greedy search, and ILP."""

    config_version: str = "engine-v1"
    weight_scale_ppm: int = 1_000_000
    annual_carry_rate_bps: int = 500
    signup_progress_pool_bps: int = 2_000
    desired_headroom_bps: int = 1_000
    minimum_headroom_cents: int = 50_000
    utilization_bands: tuple[UtilizationBand, ...] = DEFAULT_UTILIZATION_BANDS
    over_limit_penalty_points_per_bps: int = 50
    near_binding_threshold_cents: int = 10_000
    greedy_repair_depth: int = 2
    local_search_max_passes: int = 20
    ilp_timeout_seconds: int = 5
    ilp_wall_timeout_seconds: int = 60
    ilp_max_card_states: int = 5_000
    ilp_combined_tie_break_limit: int = 1_000_000
    frontier_two_goal_steps: int = 5
    frontier_three_goal_denominator: int = 4
    frontier_max_solves: int = 15
    frontier_timeout_seconds: int = 15
    cbc_exact_integer_limit: int = (2**53) - 1

    def __post_init__(self) -> None:
        if not isinstance(self.config_version, str) or not self.config_version.strip():
            raise ValueError("config_version must be a nonempty string")
        _require_int("weight_scale_ppm", self.weight_scale_ppm, minimum=1)
        _require_int(
            "annual_carry_rate_bps",
            self.annual_carry_rate_bps,
            minimum=0,
            maximum=10_000,
        )
        _require_int(
            "signup_progress_pool_bps",
            self.signup_progress_pool_bps,
            minimum=0,
            maximum=10_000,
        )
        _require_int(
            "desired_headroom_bps",
            self.desired_headroom_bps,
            minimum=0,
            maximum=10_000,
        )
        _require_int("minimum_headroom_cents", self.minimum_headroom_cents, minimum=0)
        _require_int(
            "over_limit_penalty_points_per_bps",
            self.over_limit_penalty_points_per_bps,
            minimum=0,
        )
        _require_int(
            "near_binding_threshold_cents",
            self.near_binding_threshold_cents,
            minimum=0,
        )
        _require_int("greedy_repair_depth", self.greedy_repair_depth, minimum=0)
        _require_int("local_search_max_passes", self.local_search_max_passes, minimum=1)
        _require_int("ilp_timeout_seconds", self.ilp_timeout_seconds, minimum=1)
        _require_int(
            "ilp_wall_timeout_seconds",
            self.ilp_wall_timeout_seconds,
            minimum=1,
            maximum=60,
        )
        _require_int("ilp_max_card_states", self.ilp_max_card_states, minimum=2)
        _require_int(
            "ilp_combined_tie_break_limit",
            self.ilp_combined_tie_break_limit,
            minimum=1,
        )
        _require_int("frontier_two_goal_steps", self.frontier_two_goal_steps, minimum=2)
        _require_int(
            "frontier_three_goal_denominator",
            self.frontier_three_goal_denominator,
            minimum=1,
        )
        _require_int("frontier_max_solves", self.frontier_max_solves, minimum=1)
        _require_int("frontier_timeout_seconds", self.frontier_timeout_seconds, minimum=1)
        _require_int("cbc_exact_integer_limit", self.cbc_exact_integer_limit, minimum=1)

        if not self.utilization_bands:
            raise ValueError("utilization_bands cannot be empty")
        endpoints = [band.upper_bps for band in self.utilization_bands]
        slopes = [band.penalty_points_per_bps for band in self.utilization_bands]
        if endpoints != sorted(set(endpoints)):
            raise ValueError("utilization band endpoints must be strictly increasing")
        if endpoints[-1] != 10_000:
            raise ValueError("the final utilization band must end at 10,000 bps")
        if slopes != sorted(slopes):
            raise ValueError("utilization penalty slopes must be nondecreasing")
        if self.over_limit_penalty_points_per_bps < slopes[-1]:
            raise ValueError("over-limit penalty slope cannot be lower than the final band slope")


DEFAULT_ENGINE_CONFIG = EngineConfig()


def engine_config_hash(config: EngineConfig = DEFAULT_ENGINE_CONFIG) -> str:
    """Return a stable SHA-256 hash for eval/report provenance."""

    canonical = json.dumps(
        asdict(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()
