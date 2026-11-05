from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class BuyNowIn(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(1, ge=1, le=10)


class PayIn(BaseModel):
    channel: Literal["zpay_alipay"]


class PriceIn(BaseModel):
    amount: str = Field(max_length=20)
    reason: str = Field(min_length=1, max_length=500)


class DeliverIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    attachment_id: uuid.UUID | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
