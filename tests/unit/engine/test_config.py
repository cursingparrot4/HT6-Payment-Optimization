from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from engine.config import (
    DEFAULT_ENGINE_CONFIG,
    EngineConfig,
    UtilizationBand,
    engine_config_hash,
)


def test_default_config_has_convex_bands_and_stable_hash() -> None:
    endpoints = [band.upper_bps for band in DEFAULT_ENGINE_CONFIG.utilization_bands]
    slopes = [band.penalty_points_per_bps for band in DEFAULT_ENGINE_CONFIG.utilization_bands]

    assert endpoints == [3_000, 5_000, 7_500, 10_000]
    assert slopes == [0, 2, 6, 20]
    assert engine_config_hash() == engine_config_hash(EngineConfig())
    assert len(engine_config_hash()) == 64


def test_config_hash_changes_with_a_calibration_assumption() -> None:
    changed = EngineConfig(signup_progress_pool_bps=2_500)
    assert engine_config_hash(changed) != engine_config_hash(DEFAULT_ENGINE_CONFIG)


def test_config_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_ENGINE_CONFIG.ilp_timeout_seconds = 10  # type: ignore[misc]


@pytest.mark.parametrize(
    "bands, message",
    [
        (
            (
                UtilizationBand(5_000, 2),
                UtilizationBand(3_000, 3),
                UtilizationBand(10_000, 20),
            ),
            "strictly increasing",
        ),
        (
            (UtilizationBand(3_000, 2), UtilizationBand(10_000, 1)),
            "nondecreasing",
        ),
        ((UtilizationBand(3_000, 0),), "end at 10,000"),
    ],
)
def test_config_rejects_invalid_utilization_curves(
    bands: tuple[UtilizationBand, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        EngineConfig(utilization_bands=bands)


def test_config_rejects_bool_and_out_of_range_numeric_values() -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        EngineConfig(ilp_timeout_seconds=True)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="at most 10000"):
        EngineConfig(annual_carry_rate_bps=10_001)
