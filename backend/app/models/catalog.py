from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Computed, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    title_i18n: Mapped[dict[str, Any]]
    description_i18n: Mapped[dict[str, Any]]
    images: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    floor_price_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    delivery_mode: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    sold: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    # Generated columns (DDL in the migration); never written, loaded only when referenced.
    search_text: Mapped[str | None] = mapped_column(
        Text, Computed("see migration", persisted=True), deferred=True
    )
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("see migration", persisted=True), deferred=True
    )
