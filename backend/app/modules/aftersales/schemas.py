from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Reason = Literal["not_received", "invalid_code", "wrong_item", "account_issue", "other"]
Status = Literal["open", "processing", "replaced", "refunded", "rejected", "resolved"]


class OpenIn(BaseModel):
    reason: Reason
    description: str = Field(min_length=1, max_length=2000)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)

    @field_validator("description")
    @classmethod
    def _d(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description is required")
        return v


class ActionIn(BaseModel):
    action: Literal["processing", "replace", "refund", "reject", "resolve"]
    note: str | None = Field(None, max_length=2000)
    delivery_text: str | None = Field(None, max_length=5000)
    # refund: the staff's confirmation that the money was sent back (provider refund id,
    # transaction hash or transfer note); required for action=refund (checked in the service)
    reference: str | None = Field(None, max_length=200)
    # auto_card replace: which delivered cards to void and replace (default: all of the order)
    card_ids: list[uuid.UUID] | None = Field(None, max_length=100)


class RefundIn(BaseModel):
    """Staff confirmation that the money was returned (v8 B)."""

    reference: str = Field(min_length=3, max_length=200)
    note: str | None = Field(None, max_length=2000)

    @field_validator("reference")
    @classmethod
    def _r(cls, v: str) -> str:
        v = v.strip()
        if not 3 <= len(v) <= 200:
            raise ValueError("reference must be 3-200 characters")
        return v
