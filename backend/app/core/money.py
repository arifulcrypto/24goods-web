"""Money helpers: integer minor units <-> 2-decimal strings. Never float."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_MINOR = 10**12  # 10 billion units: far beyond any MVP order


class MoneyError(ValueError):
    pass


def parse_amount(value: str | int | Decimal) -> int:
    """Parse a positive decimal amount with at most two decimals into minor units."""
    if isinstance(value, float):  # pragma: no cover - defensive, typing forbids it
        raise MoneyError("float amounts are not accepted")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("invalid amount") from exc
    if not d.is_finite() or d <= 0:
        raise MoneyError("amount must be positive")
    exponent = d.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise MoneyError("at most two decimals")
    minor = int(d * 100)
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
