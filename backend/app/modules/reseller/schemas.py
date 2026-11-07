from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LOCALES = ("en", "zh-CN")
_LOGO = re.compile(r"^/(media|api/v1/files)/[A-Za-z0-9._/-]{1,200}$")


def _logo(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip()
    if ".." in v or not _LOGO.match(v):
        raise ValueError("logo_url must be one of our own uploaded files")
    return v


def _welcome(v: dict[str, str] | None) -> dict[str, str] | None:
    if v is None:
        return None
    if set(v) - set(LOCALES):
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    out = {k: (x or "").strip() for k, x in v.items()}
    if any(len(x) > 300 for x in out.values()):
        raise ValueError("at most 300 characters")
    return out


class ApplyIn(BaseModel):
    slug: str = Field(min_length=3, max_length=32)
    store_name: str = Field(min_length=1, max_length=64)
    contact: str | None = Field(None, max_length=200)
    turnstile_token: str | None = Field(None, max_length=2048)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        from app.core.storefront import SLUG_RE

        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError("3-32 chars: a-z 0-9 and '-', not at the ends")
        return v

    @field_validator("store_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v or any(ord(c) < 32 for c in v):
            raise ValueError("invalid store name")
        return v


class PayoutAccount(BaseModel):
    method: Literal["alipay"] = "alipay"
    name: str = Field(min_length=1, max_length=64)
    account: str = Field(min_length=1, max_length=128)


class MePatch(BaseModel):
    store_name: str | None = Field(None, min_length=1, max_length=64)
    logo_url: str | None = Field(None, max_length=500)
    welcome_i18n: dict[str, str] | None = None
    contact: str | None = Field(None, max_length=200)
    markup_kind: Literal["percent", "fixed"] | None = None
    markup_value: int | None = Field(None, ge=0, le=100_000_000)
    payout_account: PayoutAccount | None = None

    _logo_ok = field_validator("logo_url")(_logo)
    _welcome_ok = field_validator("welcome_i18n")(_welcome)


class ProductPut(BaseModel):
    markup_kind: Literal["percent", "fixed"] | None = None
    markup_value: int | None = Field(None, ge=0, le=100_000_000)
    hidden: bool | None = None


class PayoutIn(BaseModel):
    amount_minor: int = Field(gt=0, le=10**10)


class AdminResellerPatch(BaseModel):
    status: Literal["active", "suspended"] | None = None
    markup_kind: Literal["percent", "fixed"] | None = None
    markup_value: int | None = Field(None, ge=0, le=100_000_000)


class DecideIn(BaseModel):
    action: Literal["paid", "rejected"]
    note: str = Field("", max_length=200)
    force: bool = False  # pay out even if it exceeds the balance recomputed now (audited)


class ResolveIn(BaseModel):
    action: Literal["activate", "refund"]
    note: str = Field("", max_length=200)


__all__ = [
    "AdminResellerPatch",
    "ApplyIn",
    "DecideIn",
    "MePatch",
    "PayoutIn",
    "ProductPut",
]


class DomainIn(BaseModel):
    hostname: str = Field(min_length=3, max_length=253)
