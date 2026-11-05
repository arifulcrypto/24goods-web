"""Batched order views (pydantic-free: shared by the API and the delivery worker)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AfterSales, Attachment, Order, Product
from app.modules.common.views import after_sales_view, order_view


async def latest_tickets(db: AsyncSession, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Most recent after-sales ticket per order, as views."""
    if not order_ids:
        return {}
    rows = list(
        await db.scalars(
            select(AfterSales)
            .where(AfterSales.order_id.in_(order_ids))
            .order_by(AfterSales.created_at.desc(), AfterSales.id)
        )
    )
    latest: dict[uuid.UUID, AfterSales] = {}
    for t in rows:
        latest.setdefault(t.order_id, t)
    att_ids = {a for t in latest.values() for a in t.attachment_ids}
    atts: dict[uuid.UUID, Attachment] = {}
    if att_ids:
        atts = {
            a.id: a for a in await db.scalars(select(Attachment).where(Attachment.id.in_(att_ids)))
        }
    return {
        oid: after_sales_view(t, [atts[a] for a in t.attachment_ids if a in atts])
        for oid, t in latest.items()
    }


async def order_views(
    db: AsyncSession, orders: list[Order], *, owner_id: uuid.UUID | None, lang: str = "en"
) -> list[dict[str, Any]]:
    att_ids = {o.manual_delivery_attachment_id for o in orders if o.manual_delivery_attachment_id}
    atts: dict[uuid.UUID, Attachment] = {}
    if att_ids:
        atts = {
            a.id: a for a in await db.scalars(select(Attachment).where(Attachment.id.in_(att_ids)))
        }
    pids = {o.product_id for o in orders if o.product_id}
    products: dict[uuid.UUID, Product] = {}
    if pids:
        products = {p.id: p for p in await db.scalars(select(Product).where(Product.id.in_(pids)))}
    tickets = await latest_tickets(db, [o.id for o in orders])
    return [
        order_view(
            o,
            viewer_is_owner=owner_id is not None and o.buyer_id == owner_id,
            attachment=atts.get(o.manual_delivery_attachment_id)
            if o.manual_delivery_attachment_id
            else None,
            product=products.get(o.product_id) if o.product_id else None,
            ticket=tickets.get(o.id),
            lang=lang,
        )
        for o in orders
    ]


async def one_view(
    db: AsyncSession, order: Order, owner_id: uuid.UUID | None, lang: str = "en"
) -> dict[str, Any]:
    return (await order_views(db, [order], owner_id=owner_id, lang=lang))[0]
