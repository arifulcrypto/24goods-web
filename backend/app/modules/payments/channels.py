"""Payment channels and the buyer-paid channel surcharge (docs/review-v4.md §1).

Config lives in ``tenants.settings.payments.channels``::

    {"zpay_alipay": {"enabled": true, "surcharge_permille": 30}}

A missing config means ``zpay_alipay`` is enabled at ``DEFAULT_SURCHARGE_PERMILLE``;
the ``usdt_*`` channels (self-hosted BEpusdt, docs/review-v5.2.md s7) are DISABLED by default
with a 0 surcharge.
Framework-free: the fee maths is integer-only and shared by orders, checkouts and tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEFAULT_CHANNEL = "zpay_alipay"
DEFAULT_SURCHARGE_PERMILLE = 30
MAX_SURCHARGE_PERMILLE = 200
USDT_CHANNELS: dict[str, str] = {  # channel id -> BEpusdt trade_type
    "usdt_arbitrum": "usdt.arbitrum",
    "usdt_xlayer": "usdt.xlayer",
    "usdt_trc20": "usdt.trc20",
}
LABELS: dict[str, dict[str, str]] = {
    DEFAULT_CHANNEL: {"en": "Alipay", "zh-CN": "支付宝"},
    "usdt_arbitrum": {"en": "USDT (Arbitrum One)", "zh-CN": "USDT（Arbitrum One）"},
    "usdt_xlayer": {"en": "USDT (X Layer)", "zh-CN": "USDT（X Layer）"},
    "usdt_trc20": {"en": "USDT (TRON TRC20)", "zh-CN": "USDT（波场 TRC20）"},
}


def is_usdt(channel: str | None) -> bool:
    return channel in USDT_CHANNELS


def provider(channel: str | None) -> str:
    """Ledger/gateway family of a channel: ``bepusdt`` or ``zpay``."""
    return "bepusdt" if is_usdt(channel) else "zpay"


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
    default_on = not is_usdt(channel)
    enabled = raw.get("enabled", default_on)
    permille = raw.get("surcharge_permille")
    return ChannelCfg(
        id=channel,
        enabled=enabled if isinstance(enabled, bool) else default_on,
        permille=permille
        if _valid_permille(permille)
        else (0 if is_usdt(channel) else DEFAULT_SURCHARGE_PERMILLE),
    )


def entry_cfg(tenant_settings: dict[str, Any] | None) -> ChannelCfg:
    """Channel used to price an order/checkout BEFORE the buyer picks one (fee is re-applied at
    pay time): the default channel if enabled, else the first enabled one, else the default
    (disabled)."""
    for cid in LABELS:
        cfg = channel_cfg(tenant_settings, cid)
        if cfg.enabled:
            return cfg
    return channel_cfg(tenant_settings, DEFAULT_CHANNEL)


def channel_by_id(tenant_settings: dict[str, Any] | None, channel: str | None) -> ChannelCfg:
    """The buyer-selected channel if it is enabled, else the entry channel (H)."""
    if channel in LABELS:
        cfg = channel_cfg(tenant_settings, channel)
        if cfg.enabled:
            return cfg
    return entry_cfg(tenant_settings)


def lines_totals(permille: int, lines: list[tuple[int, int]]) -> tuple[int, int]:
    """``(fee_minor, total_minor)`` for ``[(subtotal, discount)]`` order lines: the fee is
    ceiled PER LINE (== what create_checkout charges), never on the sum (issue 23 bug A)."""
    fee = total = 0
    for sub, disc in lines:
        f, t = split_amount(sub, permille, disc)
        fee += f
        total += t
    return fee, total


def totals_by_channel(
    tenant_settings: dict[str, Any] | None, lines: list[tuple[int, int]]
) -> dict[str, dict[str, Any]]:
    """Server truth per enabled channel: ``{id: {fee_minor, amount_minor, fee_rate}}``."""
    out: dict[str, dict[str, Any]] = {}
    for c in all_channels(tenant_settings):
        if not c.enabled:
            continue
        fee, total = lines_totals(c.permille, lines)
        out[c.id] = {"fee_minor": fee, "amount_minor": total, "fee_rate": fee_rate(c.permille)}
    return out


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
