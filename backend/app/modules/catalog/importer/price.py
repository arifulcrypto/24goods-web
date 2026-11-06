"""Price extraction + the import markup rule (integer / Decimal math only)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_MINOR = 100_000_000_00  # sanity cap: 100 million major units

_SYMBOL = re.compile(r"([¥￥$]|US\$|CNY|RMB|USD)\s*(\d[\d,]*(?:\.\d{1,2})?)")
_LABEL = re.compile(
    r"(?:价格|售价|价钱|现价|单价|price)\s*[:：]?\s*(?:[¥￥$]|US\$|CNY|RMB|USD)?\s*(\d[\d,]*(?:\.\d{1,2})?)",
    re.I,
)
_YUAN = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)\s*元")
_BARE = re.compile(r"^\s*(?:[¥￥$])?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:元|起)?\s*$")


def currency_of(symbol: str | None) -> str:
    s = (symbol or "").upper()
    return "USD" if s in ("$", "US$", "USD") else "CNY"


def to_minor(raw: Any) -> int | None:
    try:
        d = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d <= 0:
        return None
    minor = int((d * 100).to_integral_value())
    return minor if 0 < minor <= MAX_MINOR else None


def find_price(text: str) -> tuple[int, str] | None:
    """Most meaningful price in free text: labelled > symbol-prefixed > ``N元``."""
    for rx, cur in ((_LABEL, None), (_SYMBOL, None), (_YUAN, "CNY")):
        for m in rx.finditer(text):
            minor = to_minor(m.group(m.lastindex))
            if minor:
                whole = m.group(0)
                sym = re.search(r"[¥￥$]|US\$|USD|CNY|RMB", whole)
                return minor, cur or currency_of(sym.group(0) if sym else None)
    return None


def price_only(text: str) -> bool:
    return bool(_BARE.match(text))


def bare_price(text: str) -> tuple[int, str] | None:
    """Text of a dedicated price element (``¥ 4.80`` / ``4.80``)."""
    m = _BARE.match(text.strip())
    if m:
        minor = to_minor(m.group(1))
        if minor:
            return minor, "USD" if "$" in text else "CNY"
    return find_price(text)


def suggest_price(cost_minor: int | None, cfg: dict[str, Any]) -> int | None:
    """``roundup(cost + markup, round_to_minor)``; percent markup is permille."""
    if not cost_minor or cost_minor <= 0:
        return None
    value = int(cfg.get("markup_value", 0))
    if cfg.get("markup_kind") == "fixed":
        raw = cost_minor + value
    else:
        raw = -(-cost_minor * (1000 + value) // 1000)  # ceil
    step = max(1, int(cfg.get("round_to_minor", 1)))
    return -(-raw // step) * step
