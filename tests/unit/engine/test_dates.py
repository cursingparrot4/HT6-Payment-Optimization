from datetime import date

import pytest

from engine.dates import interest_free_float_days, next_statement_close, payment_due_date


def test_statement_close_is_on_or_after_purchase() -> None:
    assert next_statement_close(date(2026, 8, 11), 12) == date(2026, 8, 12)
    assert next_statement_close(date(2026, 8, 12), 12) == date(2026, 8, 12)
    assert next_statement_close(date(2026, 8, 13), 12) == date(2026, 9, 12)


def test_due_date_is_strictly_after_close() -> None:
    assert payment_due_date(date(2026, 8, 12), 20) == date(2026, 8, 20)
    assert payment_due_date(date(2026, 8, 12), 12) == date(2026, 9, 12)
    assert payment_due_date(date(2026, 8, 12), 7) == date(2026, 9, 7)


def test_statement_and_due_dates_roll_over_year() -> None:
    close = next_statement_close(date(2026, 12, 20), 12)
    due = payment_due_date(close, 7)

    assert close == date(2027, 1, 12)
    assert due == date(2027, 2, 7)
    assert interest_free_float_days(date(2026, 12, 20), 12, 7) == 49


@pytest.mark.parametrize("invalid", [0, 29, True, 12.0])
def test_card_days_are_validated(invalid: object) -> None:
    with pytest.raises(ValueError):
        next_statement_close(date(2026, 8, 1), invalid)  # type: ignore[arg-type]
