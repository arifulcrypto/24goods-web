"""Reseller sign-up settlement (review-v5 s7).

Called from ``settle_member`` (inside the payment transaction, order row already locked) when
a ``kind='reseller_signup'`` order is paid: the reseller store becomes ``active`` and the order
``completed`` immediately. Kept free of ``orders.service`` so ``payments.settle`` can import it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storefront
from app.core.audit import audit
from app.models import Message, Order, Reseller
from app.modules.chat.messages import post_system
from app.modules.orders.events import add_event, snapshot


async def activate_signup(db: AsyncSession, order: Order) -> Message | None:
    now = datetime.now(timezone.utc)
    r = await db.scalar(
        select(Reseller)
        .where(Reseller.signup_order_id == order.id, Reseller.tenant_id == order.tenant_id)
        .with_for_update()
    )
    before = snapshot(order)
    prev = order.status
    order.status = "completed"
    order.completed_at = now
    order.version += 1
    await add_event(
        db, order, prev, "completed", actor_kind="gateway", reason="reseller_signup_paid"
    )
    if r is None or r.status != "pending_payment":
        # paid for a store that is already active / suspended / gone: money is booked as
        # sign-up revenue, nothing else changes; flag it for a human
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="reseller.signup_paid_unexpected",
            target=f"order:{order.id}",
            detail={"reseller_status": r.status if r else None},
            severity="warning",
        )
        return None
    r.status = "active"
    r.activated_at = now
    r.updated_at = now
    storefront.invalidate()
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="reseller.activated",
        actor_role="gateway",
        target=f"reseller:{r.id}",
        before={"status": "pending_payment"},
        after={"status": "active"},
        detail={"order_id": str(order.id), "before_order": before},
    )
    return await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="reseller_activated",
        text=f"Your reseller store “{r.store_name}” is now active.",
        order_id=str(order.id),
    )
