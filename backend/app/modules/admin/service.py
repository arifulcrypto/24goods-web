"""Admin dashboard queries: stats, conversations, agents, ledger, audit."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import NotFound, Validation
from app.core.money import fmt
from app.core.security import STAFF_ROLES
from app.models import (
    AgentStatus,
    AuditLog,
    CardStock,
    Conversation,
    LedgerEntry,
    Order,
    Product,
    User,
)
from app.modules.chat.messages import post_system
from app.modules.chat.service import conversation_views
from app.modules.common.views import iso, product_summary

LOW_STOCK = 5


async def stats(db: AsyncSession, lang: str) -> dict[str, Any]:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    orders_today = await db.scalar(select(func.count(Order.id)).where(Order.created_at >= midnight))
    revenue = await db.scalar(
        select(func.coalesce(func.sum(Order.amount_minor), 0)).where(
            Order.paid_at >= midnight, Order.status.in_(("paid", "delivered", "completed"))
        )
    )
    pending = await db.scalar(select(func.count(Order.id)).where(Order.status == "pending_payment"))
    awaiting = await db.scalar(
        select(func.count(Order.id)).where(
            or_(
                and_(Order.status == "paid", Order.delivery_mode == "manual"),
                Order.status == "manual_review",
            )
        )
    )
    open_convs = await db.scalar(
        select(func.count(Conversation.id)).where(Conversation.status == "open")
    )
    avail = (
        select(CardStock.product_id, func.count(CardStock.id).label("n"))
        .where(CardStock.status == "available")
        .group_by(CardStock.product_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Product, func.coalesce(avail.c.n, 0))
            .outerjoin(avail, avail.c.product_id == Product.id)
            .where(
                Product.status == "active",
                Product.delivery_mode == "auto_card",
                func.coalesce(avail.c.n, 0) < LOW_STOCK,
            )
            .order_by(func.coalesce(avail.c.n, 0), Product.id)
            .limit(20)
        )
    ).all()
    return {
        "orders_today": int(orders_today or 0),
        "revenue_today": fmt(int(revenue or 0)),
        "currency": "CNY",
        "pending_payment": int(pending or 0),
        "awaiting_manual": int(awaiting or 0),
        "open_conversations": int(open_convs or 0),
        "low_stock": [product_summary(p, lang, int(n)) for p, n in rows],
    }


async def list_conversations(
    db: AsyncSession, staff: User, page: Page, filter_: str, status: str | None, lang: str
) -> dict[str, Any]:
    stmt = select(Conversation)
    if filter_ == "mine":
        stmt = stmt.where(Conversation.assigned_agent_id == staff.id)
    elif filter_ == "unassigned":
        stmt = stmt.where(Conversation.assigned_agent_id.is_(None))
    if status:
        stmt = stmt.where(Conversation.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Conversation.updated_at.desc(), Conversation.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    return page.wrap(await conversation_views(db, rows, viewer="staff", lang=lang), int(total))


async def assign(
    db: AsyncSession, conv: Conversation, agent_id: uuid.UUID | None, staff: User, ip: str | None
):
    agent: User | None = None
    if agent_id is not None:
        agent = await db.get(User, agent_id)
        if agent is None or agent.role not in STAFF_ROLES:
            raise Validation("agent_id must be a staff member")
    conv.assigned_agent_id = agent.id if agent else None
    conv.updated_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=conv.tenant_id,
        action="conversation.assigned",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"conversation:{conv.id}",
        ip=ip,
        detail={"agent_id": str(agent_id) if agent_id else None},
    )
    msg = None
    if agent is not None:
        msg = await post_system(
            db,
            tenant_id=conv.tenant_id,
            conversation_id=conv.id,
            event="agent_joined",
            agent=agent.display_name,
        )
    await db.commit()
    return msg


async def close(db: AsyncSession, conv: Conversation, staff: User, ip: str | None):
    conv.status = "closed"
    conv.updated_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=conv.tenant_id,
        action="conversation.closed",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"conversation:{conv.id}",
        ip=ip,
    )
    msg = await post_system(db, tenant_id=conv.tenant_id, conversation_id=conv.id, event="closed")
    await db.commit()
    return msg


def agent_status_view(row: AgentStatus | None) -> dict[str, Any]:
    if row is None:
        return {"status": "offline", "message": None, "updated_at": iso(datetime.now(timezone.utc))}
    return {"status": row.status, "message": row.message, "updated_at": iso(row.updated_at)}


async def get_agent_status(db: AsyncSession, staff: User) -> dict[str, Any]:
    return agent_status_view(await db.get(AgentStatus, staff.id))


async def set_agent_status(
    db: AsyncSession, staff: User, status: str, message: str | None
) -> dict[str, Any]:
    msg = (message or "").strip() or None
    await db.execute(
        insert(AgentStatus)
        .values(user_id=staff.id, tenant_id=staff.tenant_id, status=status, message=msg)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={"status": status, "message": msg, "updated_at": func.now()},
        )
    )
    await db.commit()
    row = await db.get(AgentStatus, staff.id, populate_existing=True)
    return agent_status_view(row)


async def agents(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(User, AgentStatus.status)
            .outerjoin(AgentStatus, AgentStatus.user_id == User.id)
            .where(User.role.in_(tuple(STAFF_ROLES)), User.status == "active")
            .order_by(User.display_name)
        )
    ).all()
    return [
        {"id": str(u.id), "display_name": u.display_name, "status": s or "offline"} for u, s in rows
    ]


async def ledger(db: AsyncSession, page: Page) -> dict[str, Any]:
    total = await db.scalar(select(func.count(LedgerEntry.id))) or 0
    rows = list(
        await db.scalars(
            select(LedgerEntry)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    items = [
        {
            "txn_id": str(r.txn_id),
            "at": iso(r.created_at),
            "account": r.account,
            "amount": fmt(r.amount_minor),
            "currency": r.currency,
            "memo": r.memo,
            "order_id": str(r.order_id) if r.order_id else None,
        }
        for r in rows
    ]
    return page.wrap(items, int(total))


async def audit_page(db: AsyncSession, page: Page) -> dict[str, Any]:
    total = await db.scalar(select(func.count(AuditLog.id))) or 0
    rows = (
        await db.execute(
            select(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    items = [
        {
            "at": iso(a.created_at),
            "actor": email or a.actor_role,
            "action": a.action,
            "target": a.target,
            "ip": a.ip,
            "detail": {
                k: v
                for k, v in {
                    **(a.detail or {}),
                    "before": a.before,
                    "after": a.after,
                    "severity": a.severity,
                }.items()
                if v is not None
            },
        }
        for a, email in rows
    ]
    return page.wrap(items, int(total))


async def ensure_conversation(db: AsyncSession, conv_id: uuid.UUID) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if conv is None:
        raise NotFound("Conversation not found")
    return conv
