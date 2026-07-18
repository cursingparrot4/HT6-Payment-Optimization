"""Statement-cycle date calculations used by cashflow scoring."""

from __future__ import annotations

from datetime import date


def _validate_card_day(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 28:
        raise ValueError(f"{name} must be an integer from 1 through 28")


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def next_statement_close(purchase_date: date, statement_day: int) -> date:
    """Return the first statement close on or after a purchase date."""

    _validate_card_day("statement_day", statement_day)
    candidate = date(purchase_date.year, purchase_date.month, statement_day)
    if candidate >= purchase_date:
        return candidate
    year, month = _next_month(purchase_date.year, purchase_date.month)
    return date(year, month, statement_day)


def payment_due_date(statement_close: date, due_day: int) -> date:
    """Return the first card due date strictly after a statement close."""

    _validate_card_day("due_day", due_day)
    candidate = date(statement_close.year, statement_close.month, due_day)
    if candidate > statement_close:
        return candidate
    year, month = _next_month(statement_close.year, statement_close.month)
    return date(year, month, due_day)


def interest_free_float_days(
    purchase_date: date,
    statement_day: int,
    due_day: int,
) -> int:
    close = next_statement_close(purchase_date, statement_day)
    due = payment_due_date(close, due_day)
    days = (due - purchase_date).days
    if days <= 0:
        raise AssertionError("the modeled payment due date must follow the purchase date")
    return days
