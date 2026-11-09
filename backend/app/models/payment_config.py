"""Versioned gateway credentials and durable, private payment attempts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, LargeBinary, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class PaymentGateway(Base):
    __tablename__ = "payment_gateways"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    label: Mapped[str] = mapped_column(String(80))
    gateway: Mapped[str] = mapped_column(String(500))
    merchant_id: Mapped[str] = mapped_column(String(64))
    key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    mode: Mapped[str] = mapped_column(String(16), default="qr")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    # routing (review-v10): lower priority number is tried first; sticky mode prefers last_ok_at
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_ok_at: Mapped[datetime | None]
    last_fail_at: Mapped[datetime | None]
    last_fail_reason: Mapped[str | None] = mapped_column(String(32))


class PaymentTest(Base):
    """Admin test payment at one gateway (review-v10 §C). Never linked to an order or checkout."""

    __tablename__ = "payment_tests"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_gateways.id"))
    local_ref: Mapped[str] = mapped_column(String(32))
    gateway_ref: Mapped[str] = mapped_column(String(32), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    paid_at: Mapped[datetime | None]
    trade_no: Mapped[str | None] = mapped_column(String(128))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expires_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    local_ref: Mapped[str] = mapped_column(String(16))
    gateway_ref: Mapped[str] = mapped_column(String(64), unique=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_gateways.id"))
    channel: Mapped[str] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="preparing")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expires_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class LegacyPayUrl(Base):
    """Durable proof that a signed environment-key ``submit.php`` URL was handed out (issue #32).

    The legacy path signs a URL with the deployment's env key and records nothing else, so without
    this row the question "can money arrive for this payable through a URL we cannot see?" had to
    be guessed from the current channel, key presence or provider history, all of which can change
    after the URL was issued. Written in the same transaction that returns the URL.
    """

    __tablename__ = "legacy_pay_urls"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    local_ref: Mapped[str] = mapped_column(String(16), primary_key=True)
    issued_at: Mapped[datetime] = created_at()
