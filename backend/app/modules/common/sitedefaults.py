"""Per-tenant site settings (``tenants.settings``) with defaults.

Framework-free: shared by the API and the delivery worker.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

DEFAULT_AFTER_SALES_HOURS = 672  # 28 days
DEFAULT_AUTO_CONFIRM_HOURS = {"manual": 72, "auto_card": 24}
AFTER_SALES_REOPEN_EXTENSION = timedelta(hours=24)


def after_sales_default_hours(tenant_settings: dict[str, Any] | None) -> int:
    v = (tenant_settings or {}).get("after_sales_default_hours")
    return (
        v
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 8760
        else (DEFAULT_AFTER_SALES_HOURS)
    )


def auto_confirm_hours(tenant_settings: dict[str, Any] | None, mode: str) -> int:
    cfg = (tenant_settings or {}).get("auto_confirm_hours") or {}
    v = cfg.get(mode)
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 24 * 90:
        return v
    return DEFAULT_AUTO_CONFIRM_HOURS.get(mode, 72)


def resolve_after_sales_hours(product_hours: int | None, tenant_settings: dict | None) -> int:
    return (
        product_hours if product_hours is not None else after_sales_default_hours(tenant_settings)
    )


def delivery_deadlines(
    now: datetime,
    *,
    mode: str,
    product_hours: int | None,
    tenant_settings: dict[str, Any] | None,
) -> tuple[datetime | None, datetime]:
    """``(after_sales_until, auto_confirm_at)`` for an order delivered at ``now``.

    ``after_sales_until`` is None when the product has no after-sales (0 hours).
    """
    hours = resolve_after_sales_hours(product_hours, tenant_settings)
    until = now + timedelta(hours=hours) if hours > 0 else None
    return until, now + timedelta(hours=auto_confirm_hours(tenant_settings, mode))
