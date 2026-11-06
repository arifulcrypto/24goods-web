"""Payment channels and the buyer-paid channel surcharge (docs/review-v4.md §1).

Config lives in ``tenants.settings.payments.channels``::

    {"zpay_alipay": {"enabled": true, "surcharge_permille": 30}}

A missing config means ``zpay_alipay`` is enabled at ``DEFAULT_SURCHARGE_PERMILLE``.
Framework-free: the fee maths is integer-only and shared by orders, checkouts and tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEFAULT_CHANNEL = "zpay_alipay"
DEFAULT_SURCHARGE_PERMILLE = 30
MAX_SURCHARGE_PERMILLE = 200
LABELS: dict[str, dict[str, str]] = {DEFAULT_CHANNEL: {"en": "Alipay", "zh-CN": "支付宝"}}


@dataclass(frozen=True)
class ChannelCfg:
    id: str
    enabled: bool
    permille: int


def fee_minor(subtotal_minor: int, permille: int) -> int:
    """``ceil(subtotal * permille / 1000)`` in integers: never lose a fen."""
    return (subtotal_minor * permille + 999) // 1000


def split_amount(subtotal_minor: int, permille: int, discount_minor: int = 0) -> tuple[int, int]:
    """``(fee_minor, total_minor)`` for a subtotal.

    MONEY (review-v5 s6): the fee is charged on the DISCOUNTED subtotal, per order:
    ``fee = ceil((subtotal - discount) * permille / 1000)`` and
    ``total = subtotal - discount + fee`` (== the DB CHECK ``ck_orders_amount_split``).
    """
    net = subtotal_minor - discount_minor
    fee = fee_minor(net, permille)
    return fee, net + fee


def fee_rate(permille: int) -> str:
    """30 -> ``"3%"``, 25 -> ``"2.5%"``, 0 -> ``"0%"``."""
    pct = (Decimal(permille) / 10).normalize()
    return f"{pct:f}%"


def _valid_permille(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= MAX_SURCHARGE_PERMILLE


def channel_cfg(
    tenant_settings: dict[str, Any] | None, channel: str = DEFAULT_CHANNEL
) -> ChannelCfg:
    raw = (((tenant_settings or {}).get("payments") or {}).get("channels") or {}).get(channel)
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get("enabled", True)
    permille = raw.get("surcharge_permille")
    return ChannelCfg(
        id=channel,
        enabled=enabled if isinstance(enabled, bool) else True,
        permille=permille if _valid_permille(permille) else DEFAULT_SURCHARGE_PERMILLE,
    )


def all_channels(tenant_settings: dict[str, Any] | None) -> list[ChannelCfg]:
    return [channel_cfg(tenant_settings, cid) for cid in LABELS]


def public_channels(tenant_settings: dict[str, Any] | None, lang: str) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "label": LABELS[c.id].get(lang) or LABELS[c.id]["en"],
            "fee_rate": fee_rate(c.permille),
            "permille": c.permille,
        }
        for c in all_channels(tenant_settings)
        if c.enabled
    ]


def admin_channels(tenant_settings: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        c.id: {"enabled": c.enabled, "surcharge_permille": c.permille}
        for c in all_channels(tenant_settings)
    }
