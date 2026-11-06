"""Pydantic input models for the v5 settings sections (review-v5 §3).

Every field is optional (partial update). Values are validated here, then stored through
``settings_v5.merge`` (which also clamps/normalizes). Secrets are NOT part of these models:
they go through ``PUT /admin/site/secrets``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.settings_v5 import LOCALES, TURNSTILE_ACTIONS

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_FROM = re.compile(r"^[^@\s<>]{1,64}@[A-Za-z0-9.-]{1,253}$")


def _i18n(v: dict[str, str] | None, max_len: int) -> dict[str, str] | None:
    if v is None:
        return None
    if set(v) - set(LOCALES):
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    out = {k: (v.get(k) or "").strip() for k in LOCALES}
    if any(len(x) > max_len for x in out.values()):
        raise ValueError(f"at most {max_len} characters")
    if any(any(ord(c) < 32 and c not in "\n\t" for c in x) for x in out.values()):
        raise ValueError("control characters are not allowed")
    return out


def _logo(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return ""
    if len(v) > 500 or any(ord(c) < 32 or c.isspace() for c in v):
        raise ValueError("invalid logo url")
    if not (v.startswith("/") and not v.startswith("//")) and not v.lower().startswith("https://"):
        raise ValueError("logo_url must be https:// or a site-relative path")
    return v


class GeneralIn(BaseModel):
    site_name_i18n: dict[str, str] | None = None
    subtitle_i18n: dict[str, str] | None = None
    logo_url: str | None = None
    contact_i18n: dict[str, str] | None = None
    footer_i18n: dict[str, str] | None = None

    @field_validator("site_name_i18n")
    @classmethod
    def _n(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, 40)

    @field_validator("subtitle_i18n")
    @classmethod
    def _s(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, 120)

    @field_validator("contact_i18n", "footer_i18n")
    @classmethod
    def _c(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, 500)

    @field_validator("logo_url")
    @classmethod
    def _l(cls, v: str | None) -> str | None:
        return _logo(v)


class LegalIn(BaseModel):
    enabled: bool | None = None
    mode: Literal["checkbox", "modal"] | None = None
    page_slugs: list[str] | None = Field(None, max_length=10)

    @field_validator("page_slugs")
    @classmethod
    def _p(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not _SLUG.match(x) for x in v):
            raise ValueError("invalid page slug")
        return v


class FeaturesIn(BaseModel):
    registration: bool | None = None
    guest_checkout: bool | None = None
    cart: bool | None = None
    promo_codes: bool | None = None
    resellers: bool | None = None
    voice_messages: bool | None = None
    lookup: bool | None = None
    google_login: bool | None = None


class SecurityIn(BaseModel):
    turnstile_enabled: bool | None = None
    turnstile_site_key: str | None = Field(None, max_length=100)
    turnstile_actions: list[str] | None = None
    email_verify_on_register: bool | None = None
    google_client_id: str | None = Field(None, max_length=200)

    @field_validator("turnstile_actions")
    @classmethod
    def _a(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(x not in TURNSTILE_ACTIONS for x in v):
            raise ValueError("unknown turnstile action")
        return v

    @field_validator("turnstile_site_key", "google_client_id")
    @classmethod
    def _k(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if any(ord(c) < 33 or ord(c) == 127 for c in v):
            raise ValueError("must not contain whitespace or control characters")
        return v


class DefaultsIn(BaseModel):
    locale: Literal["en", "zh-CN"] | None = None
    max_pending_orders: int | None = Field(None, ge=1, le=50)
    order_timeout_minutes: int | None = Field(None, ge=5, le=1440)


class EmailIn(BaseModel):
    enabled: bool | None = None
    host: str | None = Field(None, max_length=253)
    port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, max_length=254)
    from_addr: str | None = Field(None, max_length=254)
    from_name: str | None = Field(None, max_length=80)
    security: Literal["starttls", "ssl", "none"] | None = None
    notify_order_paid: bool | None = None
    notify_order_delivered: bool | None = None

    @field_validator("host")
    @classmethod
    def _h(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not _HOST.match(v):
            raise ValueError("invalid SMTP host")
        return v

    @field_validator("from_addr")
    @classmethod
    def _f(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not _FROM.match(v):
            raise ValueError("invalid from address")
        return v

    @field_validator("username", "from_name")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        if v is not None and any(ord(c) < 32 for c in v):
            raise ValueError("control characters are not allowed")
        return None if v is None else v.strip()


class DeliveryIn(BaseModel):
    manual_eta_hours: int | None = Field(None, ge=1, le=720)
    manual_note_i18n: dict[str, str] | None = None

    @field_validator("manual_note_i18n")
    @classmethod
    def _n(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, 300)


class ImportIn(BaseModel):
    markup_kind: Literal["percent", "fixed"] | None = None
    markup_value: int | None = Field(None, ge=0, le=100_000_000)
    round_to_minor: int | None = Field(None, ge=1, le=10_000)


class ResellersIn(BaseModel):
    base_domain: str | None = Field(None, max_length=253)
    signup_fee_minor: int | None = Field(None, ge=0, le=100_000_000)
    default_markup_kind: Literal["percent", "fixed"] | None = None
    default_markup_value: int | None = Field(None, ge=0, le=100_000_000)
    max_markup_permille: int | None = Field(None, ge=0, le=10_000)
    max_markup_fixed_minor: int | None = Field(None, ge=0, le=100_000_000)
    min_payout_minor: int | None = Field(None, ge=0, le=100_000_000)
    settle_after_hours: int | None = Field(None, ge=0, le=8760)
    reserved_slugs: list[str] | None = Field(None, max_length=200)

    @field_validator("base_domain")
    @classmethod
    def _b(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v and not _HOST.match(v):
            raise ValueError("invalid domain")
        return v

    @field_validator("reserved_slugs")
    @classmethod
    def _r(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not _SLUG.match(x) for x in v):
            raise ValueError("invalid slug")
        return v


class AuditIn(BaseModel):
    retention_days: int | None = Field(None, ge=30, le=3650)


class EmailTemplateIn(BaseModel):
    subject_i18n: dict[str, str] | None = None
    body_i18n: dict[str, str] | None = None

    @field_validator("subject_i18n")
    @classmethod
    def _s(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _tpl_map(v, 150)

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _tpl_map(v, 5000)


def _tpl_map(v: dict[str, str] | None, max_len: int) -> dict[str, str] | None:
    if v is None:
        return None
    if set(v) - set(LOCALES):
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    if any(len(x or "") > max_len for x in v.values()):
        raise ValueError(f"at most {max_len} characters")
    return {k: (x or "").strip() for k, x in v.items()}


class EmailTemplatesIn(BaseModel):
    """Partial update; an empty string resets that locale to the built-in default."""

    verify_code: EmailTemplateIn | None = None
    password_reset: EmailTemplateIn | None = None
    order_paid: EmailTemplateIn | None = None
    order_delivered: EmailTemplateIn | None = None
