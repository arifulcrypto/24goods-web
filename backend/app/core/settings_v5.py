"""v5 tenant settings sections: defaults + normalizers (review-v5 §3).

Pure functions over ``tenants.settings`` (a JSON dict). Every reader goes through
``section(ts, name)`` so a missing or partially-written section always yields a complete,
type-correct dict. Writers (PUT /admin/site) validate with pydantic first, then store the
normalized output. Secrets are NEVER stored here: they live in ``tenant_secrets``
(AES-GCM, see ``app/core/secretbox.py``), keyed by the names in ``SECRET_NAMES``.
"""

from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

LOCALES = ("en", "zh-CN")

# tenant_secrets.name values (write-only through the admin API; responses show last4 only)
SECRET_NAMES = (
    "turnstile_secret",
    "google_client_secret",
    "smtp_password",
    "telegram_bot_token",
    "cloudflare_saas_token",
)

# Actions Turnstile can protect (review-v5 §5.1)
TURNSTILE_ACTIONS = (
    "payment_initiation",
    "site_visit",
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
        "payment_challenge_enabled": False,
        "site_visit_enabled": False,
        "challenge_preset": "standard",
        "turnstile_enabled": False,
        "turnstile_site_key": "",
        "turnstile_actions": ["login", "register", "instant_account", "password_reset"],
        "email_verify_on_register": False,
        "google_client_id": "",
        # IPs / CIDRs that skip Turnstile and the global rate limit (review-v5.2 §1)
        "staff_ip_allowlist": [],
    },
    # User defaults
    "defaults": {
        "locale": "en",
        "max_pending_orders": 2,
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
    # Telegram staff notifications (review-v5.2 §10). Token is the telegram_bot_token secret.
    "notifications": {
        "telegram_enabled": False,
        "telegram_chat_ids": [],  # int-like strings (users 123, groups -100123), at most 10
        "event_order_paid": True,
        "event_chat_message": True,
        "event_after_sales": True,
        "event_payment_review": True,
        "event_reseller_application": True,
        "event_low_stock": True,
        # buyer-side defaults for chat alerts (review-v11 W2); the buyer's own choice wins
        "buyer_sound": True,
        "buyer_desktop": False,
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
        "link_mode": "query",  # query: {site}/?store=slug | subdomain: https://slug.{base_domain}/
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
            # explicit hosts of the platform zone (docs/cloudflare-reseller-domains.md)
            "stores",
            "pay",
            "delux",
            "cpalos",
            "iij",
            "iijweb",
            "iijtestcdndns",
            "iijtestonlydns",
            "softbank",
            "softbankweb",
            "racknerd",
            "vps",
            "dmit",
            "dmitipv6",
            "666clouds",
            "akile",
            "bandwagonhost",
            "bytevirt",
            "greencloudvps",
            "greenvps",
        ],
        # Custom domains through Cloudflare for SaaS (token = cloudflare_saas_token secret)
        "custom_domains_enabled": False,
        "cf_zone_id": "",
        "fallback_origin": "stores.kesiv.com",
        "max_domains": 1,
    },
    "audit": {
        "retention_days": 365,
    },
    # SEO (review-v5.2 §6). indexing_enabled=false -> noindex everywhere + robots Disallow: /
    "seo": {
        "indexing_enabled": True,
        "index_storefronts": False,  # white-label reseller hosts are indexable only when true
        "default_description_i18n": {},
        "og_image": "",  # "" | /path | https://... (absolute URL or same-origin path)
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
    ("resellers", "max_domains"): (0, 10),
    ("audit", "retention_days"): (30, 3650),
}

_ENUMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("security", "challenge_preset"): ("low", "standard", "high"),
    ("legal", "mode"): ("checkbox", "modal"),
    ("defaults", "locale"): LOCALES,
    ("email", "security"): ("starttls", "ssl", "none"),
    ("import", "markup_kind"): ("percent", "fixed"),
    ("resellers", "default_markup_kind"): ("percent", "fixed"),
}


MAX_STAFF_IPS = 50
MAX_TG_CHATS = 10
TG_CHAT_ID = re.compile(r"^-?[0-9]{1,20}$")
IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_net(raw: str) -> IPNet | None:
    """An IP or CIDR as a network (host bits allowed); None when invalid or too broad.
    Over-broad ranges (< /16 IPv4, < /48 IPv6), unspecified, multicast and link-local are refused:
    one typo such as ``0.0.0.0/0`` would otherwise switch off Turnstile + the global limit."""
    try:
        net = ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError:
        return None
    min_prefix = 16 if net.version == 4 else 48
    if net.prefixlen < min_prefix or net.is_unspecified or net.is_multicast or net.is_link_local:
        return None
    return net


def ip_in_nets(ip: str | None, nets: list[IPNet]) -> bool:
    if not ip or not nets:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:a.b.c.d from a dual-stack socket = a.b.c.d
    return any(addr.version == n.version and addr in n for n in nets)


def ip_allowlisted(ip: str | None, entries: list[str]) -> bool:
    return ip_in_nets(ip, [n for n in map(parse_net, entries) if n is not None])


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
        if (name, key) == ("security", "staff_ip_allowlist"):
            items = [x for x in items if parse_net(x) is not None][:MAX_STAFF_IPS]
        if (name, key) == ("notifications", "telegram_chat_ids"):
            items = [x.strip() for x in items if TG_CHAT_ID.match(x.strip())][:MAX_TG_CHATS]
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
