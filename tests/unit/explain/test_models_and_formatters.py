from __future__ import annotations

import pytest
from pydantic import ValidationError

from explain.formatters import (
    format_bps,
    format_cents,
    format_days,
    format_points,
    humanize_identifier,
)
from explain.models import (
    ExplanationKind,
    ExplanationLine,
    ExplanationTone,
    ExplanationUnit,
)


def test_money_percentage_days_and_points_format_without_float() -> None:
    assert format_cents(4_200) == "$42.00"
    assert format_cents(-1_125) == "-$11.25"
    assert format_cents(1, signed=True) == "+$0.01"
    assert format_bps(1_825) == "18.25%"
    assert format_bps(-450, signed=True) == "-4.50%"
    assert format_days(1) == "1 day"
    assert format_days(-2, signed=True) == "-2 days"
    assert format_points(2_275_000) == "2,275,000 utility points"


@pytest.mark.parametrize("formatter", [format_cents, format_bps, format_days, format_points])
def test_numeric_formatters_reject_float_and_bool(formatter) -> None:
    with pytest.raises(TypeError):
        formatter(1.0)
    with pytest.raises(TypeError):
        formatter(True)


def test_identifier_humanization_is_deterministic() -> None:
    assert humanize_identifier("rent-2026_08") == "Rent 2026 08"


def test_explanation_line_requires_matching_raw_value_and_unit() -> None:
    line = ExplanationLine(
        kind=ExplanationKind.REWARD,
        tone=ExplanationTone.POSITIVE,
        label="projected-cashback",
        text="Projected cashback is $42.00.",
        raw_value=4_200,
        unit=ExplanationUnit.CENTS,
        source_path="metrics.cashback_cents",
    )
    assert line.raw_value == 4_200

    with pytest.raises(ValidationError, match="both be set"):
        ExplanationLine(
            kind=ExplanationKind.REWARD,
            tone=ExplanationTone.NEUTRAL,
            label="invalid",
            text="Invalid line.",
            raw_value=1,
            source_path="metrics.cashback_cents",
        )


def test_boolean_values_require_boolean_unit() -> None:
    with pytest.raises(ValidationError, match="boolean values"):
        ExplanationLine(
            kind=ExplanationKind.CONSTRAINT,
            tone=ExplanationTone.POSITIVE,
            label="bonus-hit",
            text="The bonus is reached.",
            raw_value=True,
            unit=ExplanationUnit.POINTS,
            source_path="card_summaries[0].bonus_hit",
        )
