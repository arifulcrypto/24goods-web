"""Per-product "order information" popup config and buyer-supplied values
(docs/review-v4.md §4). The value can hold session tokens: it is never logged and never
copied into audit snapshots (audit records only ``{"buyer_input": true}``)."""

from __future__ import annotations

from typing import Any

from app.core.errors import BuyerInputRequired, BuyerInputTooLong
from app.core.i18n import pick

MAX_VALUE = 4000
DEFAULT_MAX_LEN = 2000
DEFAULT_TITLE = {"en": "Order information", "zh-CN": "下单信息"}
_KEEP = {"\n", "\t"}
_INVISIBLE = {c: None for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)}


def clean_value(value: str | None) -> str:
    """Trim and strip control characters other than newline / tab, plus the invisible
    zero-width / BOM characters that copy-paste from chat apps brings along."""
    raw = (value or "").translate(_INVISIBLE)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in raw if c in _KEEP or (ord(c) >= 32 and ord(c) != 127)).strip()


def is_enabled(cfg: dict[str, Any] | None) -> bool:
    return bool(cfg and cfg.get("enabled"))


def normalize_value(cfg: dict[str, Any] | None, value: str | None) -> str | None:
    """Validate a buyer-supplied value against a product config. Returns the stored value
    (None when the popup is off or optional and empty)."""
    if not is_enabled(cfg):
        return None  # a value for a product without the popup is silently ignored
    assert cfg is not None
    v = clean_value(value)
    if not v:
        if cfg.get("required", True):
            raise BuyerInputRequired("Order information is required for this product")
        return None
    max_len = min(int(cfg.get("max_len") or DEFAULT_MAX_LEN), MAX_VALUE)
    if len(v) > max_len:
        raise BuyerInputTooLong(
            f"buyer_input must be at most {max_len} characters", extra={"max_len": str(max_len)}
        )
    if not cfg.get("multiline", True) and "\n" in v:
        v = " ".join(part.strip() for part in v.split("\n") if part.strip())
    return v


def public_config(cfg: dict[str, Any] | None, lang: str) -> dict[str, Any] | None:
    """Config as the storefront sees it (resolved to the request locale); None when off."""
    if not is_enabled(cfg):
        return None
    assert cfg is not None
    title = pick(cfg.get("title_i18n"), lang) or pick(DEFAULT_TITLE, lang)
    return {
        "enabled": True,
        "required": bool(cfg.get("required", True)),
        "multiline": bool(cfg.get("multiline", True)),
        "max_len": min(int(cfg.get("max_len") or DEFAULT_MAX_LEN), MAX_VALUE),
        "title": title,
        "prompt": pick(cfg.get("prompt_i18n"), lang),
        "placeholder": pick(cfg.get("placeholder_i18n"), lang),
    }


def admin_config(cfg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Config as the admin form edits it (i18n form)."""
    if not cfg:
        return None

    def full(key: str) -> dict[str, str]:
        v = cfg.get(key) or {}
        return {"en": v.get("en", ""), "zh-CN": v.get("zh-CN", "")}

    return {
        "enabled": bool(cfg.get("enabled")),
        "required": bool(cfg.get("required", True)),
        "multiline": bool(cfg.get("multiline", True)),
        "max_len": int(cfg.get("max_len") or DEFAULT_MAX_LEN),
        "title_i18n": full("title_i18n"),
        "prompt_i18n": full("prompt_i18n"),
        "placeholder_i18n": full("placeholder_i18n"),
    }
