from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class Article(Base):
    """CMS article or fixed page. ``body_html_i18n`` is produced server-side (sanitized)."""

    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    slug: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8), default="article")  # article | page | notice
    category: Mapped[str] = mapped_column(String(32), default="general")
    title_i18n: Mapped[dict[str, Any]]
    body_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    body_html_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    pinned: Mapped[bool] = mapped_column(default=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AnnouncementRead(Base):
    """v11: a user has read an announcement (``articles.kind = 'notice'``)."""

    __tablename__ = "announcement_reads"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AfterSales(Base):
    __tablename__ = "after_sales"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    attachment_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
