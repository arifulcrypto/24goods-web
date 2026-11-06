"""v5 growth models: promo codes, resellers, encrypted tenant secrets (review-v5 §10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class TenantSecret(Base):
    """AES-256-GCM sealed setting secret (SMTP password, Turnstile/Google secrets)."""

    __tablename__ = "tenant_secrets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    last4: Mapped[str | None] = mapped_column(String(4))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = updated_at()


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(8))  # percent (permille) | fixed (minor)
    value: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    min_subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    max_discount_minor: Mapped[int | None] = mapped_column(BigInteger)
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    category_slugs: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    promo_code_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("promo_codes.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), unique=True)
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id"), unique=True)
    discount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Reseller(Base):
    __tablename__ = "resellers"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    store_name: Mapped[str] = mapped_column(String(64))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    welcome_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict)
    contact: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="pending_payment")
    markup_kind: Mapped[str] = mapped_column(String(8), default="percent")
    markup_value: Mapped[int] = mapped_column(BigInteger, default=100)
    payout_account: Mapped[dict[str, Any] | None]
    signup_order_id: Mapped[uuid.UUID | None]
    activated_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ResellerPrice(Base):
    __tablename__ = "reseller_prices"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    markup_kind: Mapped[str] = mapped_column(String(8))
    markup_value: Mapped[int] = mapped_column(BigInteger)
    hidden: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = updated_at()


class ResellerPayout(Base):
    __tablename__ = "reseller_payouts"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="requested")
    account_snapshot: Mapped[dict[str, Any] | None]
    note: Mapped[str | None] = mapped_column(String(200))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
