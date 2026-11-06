"""Compatibility hook for Cloudflare Turnstile (review-v5 s5.1).

The settings/turnstile work lives on another branch (``app/core/turnstile.py``). Until both
are merged this is a no-op; afterwards it delegates, so ``promo_validate`` and
``reseller_apply`` are protected exactly when the admin enables them.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request


async def check(request: Request, ts: dict[str, Any], action: str, token: str | None) -> None:
    try:
        from app.core.turnstile import require_turnstile
    except ImportError:  # pragma: no cover - core branch not merged yet
        return
    await require_turnstile(request, ts, action, token)
