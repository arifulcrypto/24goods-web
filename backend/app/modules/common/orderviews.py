"""Batched order views (pydantic-free: shared by the API and the delivery worker)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import fmt
from app.core.settings_v5 import section
from app.models import AfterSales, Attachment, Checkout, Order, Product, PromoCode, Reseller
from app.modules.common.views import after_sales_view, order_view
from app.modules.content.site import tenant_settings
from app.modules.payments.channels import totals_by_channel


def channel_totals_view(t: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        cid: {
            "fee": fmt(v["fee_minor"]),
            "fee_rate": v["fee_rate"],
            "amount": fmt(v["amount_minor"]),
        }
        for cid, v in t.items()
    }


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
        products = {
            p.id: p
            for p in await db.scalars(
                select(Product).where(
                    Product.id.in_(pids), Product.tenant_id.in_({o.tenant_id for o in orders})
                )
            )
        }
    eta_default = 24
    if any(o.delivery_mode == "manual" for o in orders):
        eta_default = section(await tenant_settings(db, orders[0].tenant_id), "delivery")[
            "manual_eta_hours"
        ]
    tickets = await latest_tickets(db, [o.id for o in orders])
    co_ids = {o.checkout_id for o in orders if o.checkout_id}
    checkout_nos: dict[uuid.UUID, str] = {}
    if co_ids:
        checkout_nos = {
            cid: no
            for cid, no in (
                await db.execute(
                    select(Checkout.id, Checkout.checkout_no).where(Checkout.id.in_(co_ids))
                )
            ).all()
        }
    rs_ids = {o.reseller_id for o in orders if o.reseller_id}
    resellers: dict[uuid.UUID, tuple[str, str]] = {}
    if rs_ids:
        resellers = {
            rid: (slug, name)
            for rid, slug, name in (
                await db.execute(
                    select(Reseller.id, Reseller.slug, Reseller.store_name).where(
                        Reseller.id.in_(rs_ids),
                        Reseller.tenant_id.in_({o.tenant_id for o in orders}),
                    )
                )
            ).all()
        }
    promo_ids = {o.promo_code_id for o in orders if o.promo_code_id}
    promos: dict[uuid.UUID, str] = {}
    if promo_ids:
        promos = {
            pid: code
            for pid, code in (
                await db.execute(
                    select(PromoCode.id, PromoCode.code).where(
                        PromoCode.id.in_(promo_ids),
                        PromoCode.tenant_id.in_({o.tenant_id for o in orders}),
                    )
                )
            ).all()
        }
    ts_by_tenant: dict[uuid.UUID, dict] = {}
    for o in orders:
        if o.status == "pending_payment" and o.tenant_id not in ts_by_tenant:
            ts_by_tenant[o.tenant_id] = await tenant_settings(db, o.tenant_id)
    return [
        order_view(
            o,
            channel_totals=channel_totals_view(
                totals_by_channel(ts_by_tenant[o.tenant_id], [(o.subtotal_minor, o.discount_minor)])
            )
            if o.status == "pending_payment"
            else None,
            reseller=resellers.get(o.reseller_id) if o.reseller_id else None,
            promo_code=promos.get(o.promo_code_id) if o.promo_code_id else None,
            viewer_is_owner=owner_id is not None and o.buyer_id == owner_id,
            attachment=atts.get(o.manual_delivery_attachment_id)
            if o.manual_delivery_attachment_id
            else None,
            product=products.get(o.product_id) if o.product_id else None,
            ticket=tickets.get(o.id),
            lang=lang,
            viewer_is_staff=owner_id is None,
            checkout_no=checkout_nos.get(o.checkout_id) if o.checkout_id else None,
            manual_eta_default=eta_default,
        )
        for o in orders
    ]


async def one_view(
    db: AsyncSession, order: Order, owner_id: uuid.UUID | None, lang: str = "en"
) -> dict[str, Any]:
    return (await order_views(db, [order], owner_id=owner_id, lang=lang))[0]
