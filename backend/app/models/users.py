from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    email: Mapped[str] = mapped_column(String(254))
    pw_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="buyer")
    display_name: Mapped[str] = mapped_column(String(64))
    locale: Mapped[str] = mapped_column(String(8), default="en")
    status: Mapped[str] = mapped_column(String(16), default="active")
    is_instant: Mapped[bool] = mapped_column(default=False)
    email_verified: Mapped[bool] = mapped_column(default=False)
    handle: Mapped[str | None] = mapped_column(String(16))
    google_sub: Mapped[str | None] = mapped_column(String(64))
    avatar_key: Mapped[str | None] = mapped_column(String(32))
    avatar_preset: Mapped[str | None] = mapped_column(String(16))
    announcement_ack: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_login_at: Mapped[datetime | None]
    suspended_at: Mapped[datetime | None]
    suspended_until: Mapped[datetime | None]
    suspend_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at()


class AgentStatus(Base):
    __tablename__ = "agent_status"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    status: Mapped[str] = mapped_column(String(16), default="offline")
    message: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = updated_at()
