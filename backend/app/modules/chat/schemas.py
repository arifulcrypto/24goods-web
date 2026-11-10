from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ConversationIn(BaseModel):
    product_id: uuid.UUID | None = None


class MessageIn(BaseModel):
    client_msg_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    type: Literal["text", "image", "voice", "file", "product_card"]
    text: str | None = Field(None, max_length=2000)
    attachment_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None  # product_card (staff only): payload is built server-side
    reply_to: int | None = Field(None, ge=1)


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ReadIn(BaseModel):
    message_id: int = Field(ge=0)


class AssignIn(BaseModel):
    agent_id: uuid.UUID | None = None


class QuoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    amount: str = Field(max_length=20)
    qty: int = Field(1, ge=1, le=100)
    note: str | None = Field(None, max_length=1000)
    product_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None
    expires_in_minutes: int = Field(ge=5, le=1440)
