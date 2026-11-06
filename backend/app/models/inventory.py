from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class CardBatch(Base):
    """One import run: note, optional expiry, final count."""

    __tablename__ = "card_batches"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    note: Mapped[str | None] = mapped_column(String(200))
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class CardStock(Base):
    """Sealed card secrets. ``app_api`` may INSERT but never SELECT ``blob``.

    Always insert with Core ``insert(...).returning(*CardStock.public_columns())`` and
    never let the ORM load or refresh this entity in the API process.
    """

    __tablename__ = "card_stock"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    fingerprint: Mapped[str] = mapped_column(String(64))
    kid: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="available")
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("card_batches.id"))
    lookup_fp: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(String(64))
    voided_at: Mapped[datetime | None]
    voided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("card_stock.id"))

    @classmethod
    def public_columns(cls) -> tuple:
        """Columns ``app_api`` is allowed to SELECT (everything except ``blob``)."""
        return (
            cls.__table__.c.id,
            cls.__table__.c.tenant_id,
            cls.__table__.c.product_id,
            cls.__table__.c.fingerprint,
            cls.__table__.c.kid,
            cls.__table__.c.status,
            cls.__table__.c.order_id,
            cls.__table__.c.created_by,
            cls.__table__.c.created_at,
            cls.__table__.c.batch_id,
            cls.__table__.c.lookup_fp,
            cls.__table__.c.expires_at,
            cls.__table__.c.void_reason,
            cls.__table__.c.voided_at,
            cls.__table__.c.voided_by,
            cls.__table__.c.replaced_by,
        )


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    card_stock_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("card_stock.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="card")
    reveal_count: Mapped[int] = mapped_column(Integer, default=0)
    first_revealed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
