"""v5 tenant settings sections: defaults + normalizers (review-v5 §3).

Pure functions over ``tenants.settings`` (a JSON dict). Every reader goes through
``section(ts, name)`` so a missing or partially-written section always yields a complete,
type-correct dict. Writers (PUT /admin/site) validate with pydantic first, then store the
normalized output. Secrets are NEVER stored here: they live in ``tenant_secrets``
(AES-GCM, see ``app/core/secretbox.py``), keyed by the names in ``SECRET_NAMES``.
"""

from __future__ import annotations

import copy
from typing import Any

LOCALES = ("en", "zh-CN")

# tenant_secrets.name values (write-only through the admin API; responses show last4 only)
SECRET_NAMES = ("turnstile_secret", "google_client_secret", "smtp_password")

# Actions Turnstile can protect (review-v5 §5.1)
TURNSTILE_ACTIONS = (
    "login",
    "register",
    "instant_account",
    "password_reset",
    "lookup",
    "promo_validate",
    "reseller_apply",
)

DEFAULTS: dict[str, dict[str, Any]] = {
    # General: brand overrides (empty = frontend brand.ts defaults), contact, logo
    "general": {
        "site_name_i18n": {},
        "subtitle_i18n": {},
        "logo_url": "",
        "contact_i18n": {},
        "footer_i18n": {},
    },
    # Login agreement (sub2api "Login terms"): published article pages by slug
    "legal": {
        "enabled": False,
        "mode": "checkbox",  # checkbox | modal
        "page_slugs": [],  # e.g. ["terms", "privacy"]
        "updated_at": "",
    },
    # Feature toggles (enforced server-side where noted in review-v5 §3)
    "features": {
        "registration": True,
        "guest_checkout": True,
        "cart": True,
        "promo_codes": True,
        "resellers": False,
        "voice_messages": True,
        "lookup": True,
        "google_login": False,
    },
    "security": {
        "turnstile_enabled": False,
        "turnstile_site_key": "",
        "turnstile_actions": ["login", "register", "instant_account", "password_reset"],
        "email_verify_on_register": False,
        "google_client_id": "",
    },
    # User defaults
    "defaults": {
        "locale": "en",
        "max_pending_orders": 5,
        "order_timeout_minutes": 30,
    },
    # Email (SMTP). Password is the smtp_password secret.
    "email": {
        "enabled": False,
        "host": "",
        "port": 587,
        "username": "",
        "from_addr": "",
        "from_name": "",
        "security": "starttls",  # starttls | ssl | none
        "notify_order_paid": True,
        "notify_order_delivered": True,
    },
    # Manual delivery promise shown on product/cart/checkout
    "delivery": {
        "manual_eta_hours": 24,
        "manual_note_i18n": {
            "en": "Manual delivery: shipped within {hours} hours. Rush orders are not accepted.",
            "zh-CN": "人工发货：{hours} 小时内发货，不接受催单。",
        },
    },
    # Product import (middleman listing)
    "import": {
        "markup_kind": "percent",  # percent | fixed
        "markup_value": 200,  # percent: permille (200 = 20%); fixed: minor units
        "round_to_minor": 10,  # round the computed price UP to a multiple of this (1 = none)
    },
    # Reseller program
    "resellers": {
        "base_domain": "kesiv.com",
        "signup_fee_minor": 9900,
        "default_markup_kind": "percent",
        "default_markup_value": 100,  # 10%
        "max_markup_permille": 2000,  # percent markups capped at 200%
        "max_markup_fixed_minor": 1000000,
        "min_payout_minor": 1000,
        "settle_after_hours": 72,  # markup becomes withdrawable N hours after completion
        "reserved_slugs": [
            "www",
            "shop",
            "api",
            "admin",
            "mail",
            "smtp",
            "cdn",
            "static",
            "media",
            "app",
            "m",
            "help",
            "support",
            "status",
            "blog",
            "docs",
            "dev",
            "test",
            "demo",
            "staging",
            "flux",
            "tools",
            "kesiv",
        ],
    },
    "audit": {
        "retention_days": 365,
    },
}

_INT_BOUNDS: dict[tuple[str, str], tuple[int, int]] = {
    ("defaults", "max_pending_orders"): (1, 50),
    ("defaults", "order_timeout_minutes"): (5, 1440),
    ("email", "port"): (1, 65535),
    ("delivery", "manual_eta_hours"): (1, 720),
    ("import", "markup_value"): (0, 100_000_000),
    ("import", "round_to_minor"): (1, 10_000),
    ("resellers", "signup_fee_minor"): (0, 100_000_000),
    ("resellers", "default_markup_value"): (0, 100_000_000),
    ("resellers", "max_markup_permille"): (0, 10_000),
    ("resellers", "max_markup_fixed_minor"): (0, 100_000_000),
    ("resellers", "min_payout_minor"): (0, 100_000_000),
    ("resellers", "settle_after_hours"): (0, 8760),
    ("audit", "retention_days"): (30, 3650),
}

_ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("legal", "mode"): ("checkbox", "modal"),
    ("defaults", "locale"): LOCALES,
    ("email", "security"): ("starttls", "ssl", "none"),
    ("import", "markup_kind"): ("percent", "fixed"),
    ("resellers", "default_markup_kind"): ("percent", "fixed"),
}


def _norm_value(name: str, key: str, default: Any, raw: Any) -> Any:
    if raw is None:
        return copy.deepcopy(default)
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else default
    if isinstance(default, int):
        if not isinstance(raw, int) or isinstance(raw, bool):
            return default
        lo, hi = _INT_BOUNDS.get((name, key), (-(2**62), 2**62))
        return min(max(raw, lo), hi)
    if isinstance(default, str):
        if not isinstance(raw, str):
            return default
        allowed = _ENUMS.get((name, key))
        if allowed is not None and raw not in allowed:
            return default
        return raw
    if isinstance(default, list):
        if not isinstance(raw, list):
            return copy.deepcopy(default)
        items = [x for x in raw if isinstance(x, str)]
        if (name, key) == ("security", "turnstile_actions"):
            items = [x for x in items if x in TURNSTILE_ACTIONS]
        return list(dict.fromkeys(items))
    if isinstance(default, dict):  # *_i18n maps
        if not isinstance(raw, dict):
            return copy.deepcopy(default)
        out = {k: v for k, v in raw.items() if k in LOCALES and isinstance(v, str)}
        if key == "manual_note_i18n" and not out:
            return copy.deepcopy(default)
        return out
    return copy.deepcopy(default)


def section(ts: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """Complete, normalized copy of settings section ``name`` (unknown keys dropped)."""
    default = DEFAULTS[name]
    raw = (ts or {}).get(name)
    raw = raw if isinstance(raw, dict) else {}
    return {k: _norm_value(name, k, v, raw.get(k)) for k, v in default.items()}


def merge(ts: dict[str, Any] | None, name: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Section after applying a partial ``patch`` (None values = unchanged)."""
    cur = section(ts, name)
    cur.update({k: v for k, v in patch.items() if v is not None and k in DEFAULTS[name]})
    return section({name: cur}, name)


def manual_eta_hours(ts: dict[str, Any] | None, product_eta: int | None) -> int:
    return product_eta or section(ts, "delivery")["manual_eta_hours"]
