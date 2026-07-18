"""Deterministic display formatting with no binary-float arithmetic."""

from __future__ import annotations


def _require_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")


def format_cents(value: int, *, signed: bool = False) -> str:
    _require_int("value", value)
    sign = ""
    if value < 0:
        sign = "-"
    elif value > 0 and signed:
        sign = "+"
    absolute = abs(value)
    dollars, cents = divmod(absolute, 100)
    return f"{sign}${dollars:,}.{cents:02d}"


def format_bps(value: int, *, signed: bool = False) -> str:
    _require_int("value", value)
    sign = ""
    if value < 0:
        sign = "-"
    elif value > 0 and signed:
        sign = "+"
    absolute = abs(value)
    percent, hundredths = divmod(absolute, 100)
    return f"{sign}{percent}.{hundredths:02d}%"


def format_days(value: int, *, signed: bool = False) -> str:
    _require_int("value", value)
    sign = "+" if signed and value > 0 else ""
    unit = "day" if abs(value) == 1 else "days"
    return f"{sign}{value:,} {unit}"


def format_points(value: int, *, signed: bool = False) -> str:
    _require_int("value", value)
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value:,} utility points"


def humanize_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifier must be a nonempty string")
    return value.replace("_", " ").replace("-", " ").strip().title()
