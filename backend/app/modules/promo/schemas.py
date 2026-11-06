from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.promo.service import CODE_RE, normalize_code


class ValidateItem(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(1, ge=1, le=10)


class ValidateIn(BaseModel):
    code: str = Field(max_length=40)
    items: list[ValidateItem] = Field(min_length=1, max_length=20)
    turnstile_token: str | None = Field(None, max_length=2048)


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("timestamps need a timezone offset")
    return v


class PromoIn(BaseModel):
    code: str | None = Field(None, max_length=32)  # omitted -> random 8-char code
    kind: Literal["percent", "fixed"]
    value: int = Field(gt=0, le=100_000_000)
    min_subtotal_minor: int = Field(0, ge=0, le=10**10)
    max_discount_minor: int | None = Field(None, gt=0, le=10**10)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    usage_limit: int | None = Field(None, gt=0, le=10**9)
    per_user_limit: int = Field(1, ge=1, le=1000)
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    category_slugs: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    note: str | None = Field(None, max_length=200)
    currency: str = Field("CNY", pattern=r"^[A-Z]{3}$")

    @field_validator("code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        c = normalize_code(v)
        if not CODE_RE.match(c):
            raise ValueError("3-32 chars: A-Z 0-9 _ -")
        return c

    _tz = field_validator("starts_at", "ends_at")(_aware)

    @field_validator("category_slugs")
    @classmethod
    def _slugs(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(s.strip() for s in v if s.strip()))

    @model_validator(mode="after")
    def _check(self) -> PromoIn:
        if self.kind == "percent" and self.value > 1000:
            raise ValueError("percent value is per-mille (1..1000)")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class PromoPatch(BaseModel):
    """Partial update. ``code`` is immutable (redemptions reference it)."""

    kind: Literal["percent", "fixed"] | None = None
    value: int | None = Field(None, gt=0, le=100_000_000)
    min_subtotal_minor: int | None = Field(None, ge=0, le=10**10)
    max_discount_minor: int | None = Field(None, gt=0, le=10**10)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    usage_limit: int | None = Field(None, gt=0, le=10**9)
    per_user_limit: int | None = Field(None, ge=1, le=1000)
    product_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    category_slugs: list[str] | None = Field(None, max_length=50)
    enabled: bool | None = None
    note: str | None = Field(None, max_length=200)

    _tz = field_validator("starts_at", "ends_at")(_aware)
