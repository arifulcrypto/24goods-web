"""Money helpers: integer minor units <-> 2-decimal strings. Never float."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

MAX_MINOR = 10**12  # 10 billion units: far beyond any MVP order


class MoneyError(ValueError):
    pass


_AMOUNT = re.compile(r"^[0-9]{1,10}(?:\.[0-9]{1,2})?$")


def parse_amount(value: str | int | Decimal) -> int:
    """Parse a positive amount with at most two decimals into minor units.

    The syntax is checked BEFORE any Decimal/int conversion: exponents ("1e999999999"),
    NaN/Infinity, signs, whitespace inside and > 10 integer digits are rejected, so hostile
    input can never trigger huge-integer work or an overflow (-> 500).
    """
    if isinstance(value, bool) or isinstance(value, float):  # pragma: no cover - typing
        raise MoneyError("invalid amount")
    raw = str(value).strip()
    if len(raw) > 16 or not _AMOUNT.match(raw):
        raise MoneyError("invalid amount (digits with up to two decimals)")
    whole, _, frac = raw.partition(".")
    minor = int(whole) * 100 + int((frac + "00")[:2])
    if minor <= 0:
        raise MoneyError("amount must be positive")
    if minor > MAX_MINOR:
        raise MoneyError("amount too large")
    return minor


def to_decimal(minor: int) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))


def fmt(minor: int) -> str:
    """19 90 -> "19.90"; negative values keep their sign (ledger)."""
    return f"{to_decimal(minor):.2f}"


def percent_of(minor: int, permille: int) -> int:
    """Round-half-up fraction of an amount in per-mille (16 -> 1.6 %)."""
    return int((Decimal(minor) * permille / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
