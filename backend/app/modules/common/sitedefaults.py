"""Per-tenant site settings (``tenants.settings``) with defaults.

Framework-free: shared by the API and the delivery worker.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any

DEFAULT_AFTER_SALES = {"unit": "month", "value": 1}
DEFAULT_AFTER_SALES_HOURS = 720  # legacy hours view of the default month (~30 days)
HOURS_PER_MONTH = 30 * 24  # approximate; only for old clients, never for deadlines
DEFAULT_AUTO_CONFIRM_HOURS = {"manual": 72, "auto_card": 24}
AFTER_SALES_REOPEN_EXTENSION = timedelta(hours=24)


def add_months(dt: datetime, months: int) -> datetime:
    """Calendar-month add in the datetime's own zone (UTC in practice); the day is
    clamped to the target month's end (Jan 31 + 1 month = Feb 28/29)."""
    index = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _int(v: Any, lo: int, hi: int) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def after_sales_default(tenant_settings: dict[str, Any] | None) -> tuple[str, int]:
    """``(unit, value)``: the ``after_sales_default`` setting, else the legacy hours key,
    else one calendar month."""
    ts = tenant_settings or {}
    cfg = ts.get("after_sales_default")
    if isinstance(cfg, dict):
        if cfg.get("unit") == "month" and (m := _int(cfg.get("value"), 1, 12)) is not None:
            return "month", m
        if cfg.get("unit") == "hour" and (h := _int(cfg.get("value"), 0, 8760)) is not None:
            return "hour", h
    legacy = _int(ts.get("after_sales_default_hours"), 0, 8760)
    if legacy is not None:
        return "hour", legacy
    return "month", 1


def after_sales_default_hours(tenant_settings: dict[str, Any] | None) -> int:
    """Hours view of the tenant default (months are approximate: 30 days)."""
    unit, value = after_sales_default(tenant_settings)
    return value * HOURS_PER_MONTH if unit == "month" else value


def auto_confirm_hours(tenant_settings: dict[str, Any] | None, mode: str) -> int:
    cfg = (tenant_settings or {}).get("auto_confirm_hours") or {}
    v = cfg.get(mode)
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 24 * 90:
        return v
    return DEFAULT_AUTO_CONFIRM_HOURS.get(mode, 72)


def resolve_after_sales(
    product_hours: int | None, product_months: int | None, tenant_settings: dict | None
) -> tuple[str, int]:
    """product hours -> product months -> tenant default; returns ``(unit, value)``."""
    if product_hours is not None:
        return "hour", product_hours
    if product_months is not None:
        return "month", product_months
    return after_sales_default(tenant_settings)


def resolve_after_sales_hours(
    product_hours: int | None,
    tenant_settings: dict | None,
    product_months: int | None = None,
) -> int:
    unit, value = resolve_after_sales(product_hours, product_months, tenant_settings)
    return value * HOURS_PER_MONTH if unit == "month" else value


def resolve_after_sales_months(
    product_hours: int | None, product_months: int | None, tenant_settings: dict | None
) -> int | None:
    unit, value = resolve_after_sales(product_hours, product_months, tenant_settings)
    return value if unit == "month" else None


def delivery_deadlines(
    now: datetime,
    *,
    mode: str,
    product_hours: int | None,
    tenant_settings: dict[str, Any] | None,
    product_months: int | None = None,
) -> tuple[datetime | None, datetime]:
    """``(after_sales_until, auto_confirm_at)`` for an order delivered at ``now``.

    ``after_sales_until`` is None when the product has no after-sales (0 hours).
    """
    unit, value = resolve_after_sales(product_hours, product_months, tenant_settings)
    if unit == "month":
        until: datetime | None = add_months(now, value)
    else:
        until = now + timedelta(hours=value) if value > 0 else None
    return until, now + timedelta(hours=auto_confirm_hours(tenant_settings, mode))
