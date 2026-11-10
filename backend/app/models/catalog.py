from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Computed, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
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
    delivery_format: Mapped[str | None] = mapped_column(Text)
    delivery_separator: Mapped[str] = mapped_column(String(8), default="----")
    redeem_url: Mapped[str | None] = mapped_column(String(500))
    usage_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    usage_html_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    after_sales_hours: Mapped[int | None] = mapped_column(Integer)
    after_sales_months: Mapped[int | None] = mapped_column(SmallInteger)
    # v7: null = not a subscription; else {period_policy, period_days, grace_days, ...}
    subscription: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    group_sort: Mapped[int] = mapped_column(Integer, default=0)
    pinned_at: Mapped[datetime | None]
    # v4: per-product "order information" popup config (review-v4 §4) and markdown render
    buyer_input: Mapped[dict[str, Any] | None]
    description_html_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # v5: archive, manual-delivery ETA, import/middleman fields (staff-only)
    delivery_eta_hours: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    archived_at: Mapped[datetime | None]
    # v5.2: manual-delivery stock; NULL = unlimited (reserved at order, released on cancel/expiry)
    manual_stock: Mapped[int | None] = mapped_column(Integer)
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


class Category(Base):
    """v5: tenant-managed categories (was a hard-coded dict)."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    slug: Mapped[str] = mapped_column(String(32))
    name_i18n: Mapped[dict[str, Any]]
    icon: Mapped[str | None] = mapped_column(String(32))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    pinned_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ProductVariant(Base):
    """v11 W1: a purchasable SKU of a product (own price, stock and optional terms)."""

    __tablename__ = "product_variants"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    name_i18n: Mapped[dict[str, Any]]
    price_minor: Mapped[int] = mapped_column(BigInteger)
    floor_price_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    # NULL = unlimited (manual products); auto-card stock is counted from card_stock
    manual_stock: Mapped[int | None] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    # NULL term fields inherit the product's
    after_sales_months: Mapped[int | None] = mapped_column(SmallInteger)
    after_sales_hours: Mapped[int | None] = mapped_column(Integer)
    subscription: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    sold: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
