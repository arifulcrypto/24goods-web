from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.pow import PowIn

_SOURCE = re.compile(r"^[a-z0-9_-]{1,32}$")


class BuyNowIn(BaseModel):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None  # required when the product has active variants
    qty: int = Field(1, ge=1, le=10)
    src: str | None = Field(None, max_length=200)
    buyer_input: str | None = Field(None, max_length=4000)
    promo_code: str | None = Field(None, max_length=40)

    @field_validator("src")
    @classmethod
    def _src(cls, v: str | None) -> str | None:
        """Traffic-attribution tag: ``[a-z0-9_-]{0,32}``. Anything else is dropped, never
        stored, so a malformed link can't break checkout."""
        v = (v or "").strip().lower()
        return v if _SOURCE.match(v) else None


class PayIn(BaseModel):
    challenge_id: str | None = Field(None, max_length=64)
    pow: PowIn | None = None
    turnstile_token: str | None = Field(None, max_length=2048)
    idempotency_key: str | None = Field(None, max_length=64)
    channel: Literal["zpay_alipay", "usdt_arbitrum", "usdt_xlayer", "usdt_trc20"]


class PriceIn(BaseModel):
    amount: str = Field(max_length=20)
    reason: str = Field(min_length=1, max_length=500)
    remove_markup: bool = False


class DeliverIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    attachment_id: uuid.UUID | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReleaseHoldIn(BaseModel):
    """#72 B2: staff exit from ``manual_review``."""

    outcome: Literal["accept", "close"]
    reason: str = Field(min_length=1, max_length=200)


class BuyerCancelIn(BaseModel):
    reason: str | None = Field(None, max_length=200)


class InputIn(BaseModel):
    buyer_input: str | None = Field(max_length=4000)


class CartItemIn(BaseModel):
    product_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    qty: int = Field(1, ge=1, le=10)
    buyer_input: str | None = Field(None, max_length=4000)


class CartQuoteIn(BaseModel):
    channel: Literal["zpay_alipay", "usdt_arbitrum", "usdt_xlayer", "usdt_trc20"] | None = None
    items: list[CartItemIn] = Field(min_length=1, max_length=20)
    promo_code: str | None = Field(None, max_length=40)


class CheckoutIn(BaseModel):
    channel: Literal["zpay_alipay", "usdt_arbitrum", "usdt_xlayer", "usdt_trc20"] | None = None
    items: list[CartItemIn] = Field(min_length=1, max_length=20)
    source: str | None = Field(None, max_length=200)
    promo_code: str | None = Field(None, max_length=40)

    @field_validator("source")
    @classmethod
    def _source(cls, v: str | None) -> str | None:
        v = (v or "").strip().lower()
        return v if _SOURCE.match(v) else None
