"""After-sales (售后): buyer tickets and staff actions. Every state change is audited."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import ApiError, Conflict, Forbidden, InvalidState, NotFound, Validation
from app.core.money import fmt
from app.core.settings_v5 import section
from app.models import (
    AfterSales,
    Attachment,
    CardStock,
    Delivery,
    LedgerEntry,
    Message,
    Order,
    OrderEvent,
    Outbox,
    Product,
    User,
)
from app.modules.aftersales.schemas import ActionIn, OpenIn
from app.modules.chat.messages import post_system
from app.modules.common.orderviews import REFUND_PREFIX
from app.modules.common.sitedefaults import AFTER_SALES_REOPEN_EXTENSION
from app.modules.common.views import after_sales_view, iso
from app.modules.content.site import tenant_settings
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import get_owned, release_manual_stock
from app.modules.payments.ledger import Line, post_txn
from app.modules.payments.settle import payment_for_order, payment_lines
from app.payments.zpay import CHANNEL

ELIGIBLE = ("delivered", "completed")
# From the Nth replacement on (3rd), replacing needs an admin (agents can't loop replacements).
REPLACE_ADMIN_FROM = 3
UNDELIVERED = ("paid", "manual_review")  # paid, nothing handed over yet (v8 A.3/A.4)
OPEN_STATES = ("open", "processing")
AUTO_CARD_ESCALATE_AFTER = timedelta(hours=1)
REPLACE_TOPIC = "delivery.replace"

# status after each staff action
_TARGET = {
    "processing": "processing",
    "refund": "refunded",
    "reject": "rejected",
    "resolve": "resolved",
}


class AfterSalesClosed(ApiError):
    """The after-sales window for this order has closed"""

    status, code = 409, "after_sales_closed"


class AfterSalesOpen(ApiError):
    """This order already has an open after-sales request"""

    status, code = 409, "after_sales_open"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _snap(t: AfterSales) -> dict[str, Any]:
    return {"status": t.status, "order_id": str(t.order_id)}


async def _attachments(db: AsyncSession, ids: list[uuid.UUID]) -> list[Attachment]:
    if not ids:
        return []
    rows = {a.id: a for a in await db.scalars(select(Attachment).where(Attachment.id.in_(ids)))}
    return [rows[i] for i in ids if i in rows]


async def buyer_view(db: AsyncSession, t: AfterSales) -> dict[str, Any]:
    return after_sales_view(t, await _attachments(db, list(t.attachment_ids)))


async def open_ticket(
    db: AsyncSession, buyer: User, ref: str, body: OpenIn, ip: str | None
) -> tuple[AfterSales, Order, Message | None]:
    order = await get_owned(db, ref, buyer, lock=True)
    if order.kind == "goods" and order.status in UNDELIVERED:
        # v8 A.4: non-delivery escalation, only once the delivery promise has lapsed
        if body.reason != "not_received":
            raise InvalidState("Only a not-received report can be opened before delivery")
        if order.paid_at is None or order.paid_at + await _escalate_after(db, order) > utcnow():
            raise InvalidState(
                "This order is still within its delivery time", code="delivery_not_overdue"
            )
    else:
        if order.status not in ELIGIBLE or order.kind != "goods":
            raise InvalidState("After-sales is only available for delivered orders")
        if order.after_sales_until is None or order.after_sales_until <= utcnow():
            raise AfterSalesClosed("The after-sales period for this order has ended")
    ids = list(dict.fromkeys(body.attachment_ids))
    if ids:
        found = list(
            await db.scalars(
                select(Attachment).where(
                    Attachment.id.in_(ids),
                    Attachment.owner_id == buyer.id,
                    Attachment.kind == "image",
                )
            )
        )
        if len(found) != len(ids):
            raise NotFound("Attachment not found")
    t = AfterSales(
        tenant_id=order.tenant_id,
        order_id=order.id,
        buyer_id=buyer.id,
        reason=body.reason,
        description=body.description,
        attachment_ids=ids,
        status="open",
    )
    db.add(t)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AfterSalesOpen("This order already has an open after-sales request") from None
    order.version += 1  # serialises against the auto-confirm job (which locks the order row)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="aftersales.opened",
        actor_id=buyer.id,
        actor_role=buyer.role,
        target=f"after_sales:{t.id}",
        after=_snap(t),
        ip=ip,
        detail={"order_id": str(order.id), "reason": t.reason},
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="after_sales_opened",
        order_id=str(order.id),
        after_sales_id=str(t.id),
        reason=t.reason,
    )
    await db.commit()
    return t, order, msg


async def _escalate_after(db: AsyncSession, order: Order) -> timedelta:
    """Manual: the product's delivery ETA (else the tenant's ``delivery.manual_eta_hours``);
    auto-card: one hour."""
    if order.delivery_mode != "manual":
        return AUTO_CARD_ESCALATE_AFTER
    product = (
        await db.scalar(
            select(Product).where(
                Product.id == order.product_id, Product.tenant_id == order.tenant_id
            )
        )
        if order.product_id
        else None
    )
    hours = (product.delivery_eta_hours if product else None) or section(
        await tenant_settings(db, order.tenant_id), "delivery"
    )["manual_eta_hours"]
    return timedelta(hours=hours)


# ------------------------------------------------------------------ staff side
async def admin_views(db: AsyncSession, rows: list[AfterSales]) -> list[dict[str, Any]]:
    if not rows:
        return []
    orders = {
        o.id: o
        for o in await db.scalars(select(Order).where(Order.id.in_({t.order_id for t in rows})))
    }
    buyers = {
        u.id: u
        for u in await db.scalars(select(User).where(User.id.in_({t.buyer_id for t in rows})))
    }
    att_ids = list({a for t in rows for a in t.attachment_ids})
    atts = {a.id: a for a in await _attachments(db, att_ids)}
    out = []
    for t in rows:
        o = orders.get(t.order_id)
        b = buyers.get(t.buyer_id)
        v = after_sales_view(
            t, [atts[a] for a in t.attachment_ids if a in atts], o.order_no if o else None
        )
        v["order"] = (
            {
                "id": str(o.id),
                "order_no": o.order_no,
                "title": o.title,
                "status": o.status,
                "delivery_mode": o.delivery_mode,
                "qty": o.qty,
                "amount": fmt(o.amount_minor),
                "currency": o.currency,
                "delivered_at": iso(o.delivered_at),
                "after_sales_until": iso(o.after_sales_until),
            }
            if o
            else None
        )
        v["buyer"] = {
            "id": str(t.buyer_id),
            "display_name": b.display_name if b else "",
            "email": b.email if b else "",
        }
        v["resolved_by"] = str(t.resolved_by) if t.resolved_by else None
        out.append(v)
    return out


async def admin_list(db: AsyncSession, page: Page, status: str | None) -> dict[str, Any]:
    stmt = select(AfterSales)
    if status:
        stmt = stmt.where(AfterSales.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(AfterSales.created_at.desc(), AfterSales.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    return page.wrap(await admin_views(db, rows), int(total))


async def admin_get(db: AsyncSession, ticket_id: uuid.UUID, *, lock: bool = False) -> AfterSales:
    stmt = select(AfterSales).where(AfterSales.id == ticket_id)
    if lock:
        stmt = stmt.with_for_update()
    t = await db.scalar(stmt)
    if t is None:
        raise NotFound("After-sales request not found")
    return t


async def admin_detail(db: AsyncSession, ticket_id: uuid.UUID) -> dict[str, Any]:
    t = await admin_get(db, ticket_id)
    (view,) = await admin_views(db, [t])
    return view


def extend_auto_confirm(order: Order) -> None:
    """A closed ticket gives the buyer at least 24 h before the order auto-completes."""
    if order.status == "delivered":
        floor = utcnow() + AFTER_SALES_REOPEN_EXTENSION
        order.auto_confirm_at = max(order.auto_confirm_at or floor, floor)


async def act(
    db: AsyncSession,
    staff: User,
    ticket_id: uuid.UUID,
    body: ActionIn,
    ip: str | None,
) -> tuple[AfterSales, Order, list[Message]]:
    t = await admin_get(db, ticket_id, lock=True)
    order = await db.scalar(select(Order).where(Order.id == t.order_id).with_for_update())
    assert order is not None
    if t.status not in OPEN_STATES:
        raise InvalidState("This after-sales request is already closed")
    if body.action == "processing" and t.status == "processing":
        raise InvalidState("Already being processed")
    note = (body.note or "").strip() or None
    before = _snap(t)
    msgs: list[Message | None] = []
    detail: dict[str, Any] = {"action": body.action, "order_id": str(order.id)}
    event = None

    if body.action == "refund" and staff.role != "admin":
        raise Forbidden("Refunds require an admin")
    if body.action == "reject" and note is None:
        raise Validation("note is required to reject a request")
    reference = clean_reference(body.reference) if body.action == "refund" else None
    if reference is not None:
        detail["reference"] = reference

    if body.action == "replace":
        if order.status not in ELIGIBLE:
            raise InvalidState("Order is not in a replaceable state")
        replaced_before = (
            await db.scalar(
                select(func.count())
                .select_from(AfterSales)
                .where(AfterSales.order_id == order.id, AfterSales.status == "replaced")
            )
            or 0
        )
        if replaced_before + 1 >= REPLACE_ADMIN_FROM and staff.role != "admin":
            raise Forbidden("From the 3rd replacement on, an admin must approve it")
        if order.delivery_mode == "manual":
            text = (body.delivery_text or "").strip()
            if not text:
                raise Validation("delivery_text is required to replace a manual delivery")
            order.manual_delivery_text = text
            order.version += 1
            t.status = "replaced"
            detail["delivery_text_chars"] = len(text)
            event = "after_sales_replaced"
        else:
            live = set(
                await db.scalars(
                    select(CardStock.id)
                    .join(Delivery, Delivery.card_stock_id == CardStock.id)
                    .where(Delivery.order_id == order.id, CardStock.order_id == order.id)
                    .where(CardStock.status != "void")
                )
            )
            if not live:
                raise Validation("This order has no replaceable cards")
            chosen = list(dict.fromkeys(body.card_ids or []))
            if chosen and not set(chosen) <= live:
                raise Validation("card_ids must belong to this order's current cards")
            db.add(
                Outbox(
                    tenant_id=t.tenant_id,
                    topic=REPLACE_TOPIC,
                    key=str(t.id),
                    payload={
                        "order_id": str(order.id),
                        "after_sales_id": str(t.id),
                        "card_ids": [str(c) for c in chosen],
                        "actor_id": str(staff.id),
                        "actor_role": staff.role,
                    },
                )
            )
            t.status = "processing"  # the delivery worker sets `replaced` once it has delivered
            detail["queued"] = True
    else:
        t.status = _TARGET[body.action]
        if body.action == "refund":
            if order.kind != "goods" or order.status not in (*ELIGIBLE, *UNDELIVERED):
                raise InvalidState("This order cannot be refunded")
            assert reference is not None
            await refund_order(db, order, staff, ip, reference, note, source="after_sales")
            event = "after_sales_refunded"
        elif body.action == "reject":
            event = "after_sales_rejected"
        elif body.action == "resolve":
            event = "after_sales_resolved"

    if note is not None:
        t.resolution_note = note
    if t.status not in OPEN_STATES:
        t.resolved_by, t.resolved_at = staff.id, utcnow()
        extend_auto_confirm(order)
    t.updated_at = utcnow()
    await audit(
        db,
        tenant_id=t.tenant_id,
        action=f"aftersales.{body.action}",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"after_sales:{t.id}",
        before=before,
        after=_snap(t),
        ip=ip,
        detail=detail,
    )
    if event is not None:
        msgs.append(
            await post_system(
                db,
                tenant_id=t.tenant_id,
                conversation_id=order.conversation_id,
                event=event,
                order_id=str(order.id),
                after_sales_id=str(t.id),
                note=note,
                reference=reference,
            )
        )
    await db.commit()
    return t, order, [m for m in msgs if m is not None]


def clean_reference(raw: str | None) -> str:
    """The staff's proof that the money went back: provider refund id, tx hash or note."""
    ref = " ".join((raw or "").split())
    if not 3 <= len(ref) <= 200:
        raise Validation("reference (3-200 characters) is required: how was the money returned?")
    return ref


async def _has_open_ledger(db: AsyncSession, order: Order) -> bool:
    """True when the order's payment is booked and not yet reversed (net gateway clearing > 0)."""
    net = await db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.tenant_id == order.tenant_id,
            LedgerEntry.order_id == order.id,
            LedgerEntry.account.like("gateway_clearing:%"),
        )
    )
    return int(net or 0) > 0


async def refund_order(
    db: AsyncSession,
    order: Order,
    staff: User,
    ip: str | None,
    reference: str,
    note: str | None,
    *,
    source: str,
) -> None:
    """The one refund routine (after-sales and refund-before-delivery). Staff call it AFTER
    returning the money out-of-band; ``reference`` records how (v8 B). Order -> refunded plus
    a balanced reversal ledger transaction (sum is zero), once. The caller holds the order
    row lock and commits."""
    before = snapshot(order)
    prev = order.status
    reverse_ledger = True
    if await db.scalar(
        select(func.count(OrderEvent.id)).where(
            OrderEvent.tenant_id == order.tenant_id,
            OrderEvent.to_status == "refunded",
            OrderEvent.reason == f"{REFUND_PREFIX}{reference}"[:500],
            OrderEvent.order_id != order.id,
        )
    ):
        raise Conflict("This refund reference was already used for another order")
    pay = await payment_for_order(db, order)
    if prev in UNDELIVERED:
        # a payment-anomaly manual_review order may never have been booked: never reverse
        # a posting that does not exist (would invent revenue)
        reverse_ledger = await _has_open_ledger(db, order)
        if not reverse_ledger and (pay is None or pay.status != "succeeded"):
            raise InvalidState("No recorded payment for this order; cancel it instead")
        await release_manual_stock(db, order.tenant_id, [order])  # undelivered: stock goes back
    order.status = "refunded"
    order.version += 1
    await add_event(
        db,
        order,
        prev,
        "refunded",
        actor_id=staff.id,
        reason=f"{REFUND_PREFIX}{reference}"[:500],
    )
    if reverse_ledger:
        await post_txn(
            db,
            tenant_id=order.tenant_id,
            currency=order.currency,
            order_id=order.id,
            memo=f"refund {order.order_no}",
            # MONEY: the exact mirror of the payment lines (sales_revenue, reseller_payable,
            # promo_discount, surcharge_revenue, gateway_clearing), so a refund can never
            # leave a residue on any account. A paid-out reseller balance may go negative
            # (clawback).
            lines=[
                Line(ln.account, -ln.amount_minor)
                for ln in reversed(payment_lines(order, order.channel or CHANNEL))
            ],
        )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.refunded",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={
            "recorded": True,
            "source": source,
            "reference": reference,
            "note": (note or "")[:500] or None,
            "from_status": prev,
            "ledger_reversed": reverse_ledger,
            "payment_id": str(pay.id) if pay else None,
            "paid_minor": pay.amount_minor if pay else None,
        },
        severity="warning",
    )
