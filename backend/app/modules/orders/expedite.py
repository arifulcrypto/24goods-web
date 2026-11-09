"""Buyer "Remind seller to ship" on a paid, undelivered manual order (review-v9 §6).

Limits: one nudge per 30 minutes per order, three per order. The count lives in order_events
(reason ``expedite``), so it survives restarts and needs no extra table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import notify_tg
from app.core.audit import audit
from app.core.errors import ApiError, InvalidState, RateLimited
from app.core.resources import Resources
from app.models import Message, Order, OrderEvent, User
from app.modules.chat.messages import post_system
from app.modules.orders import service
from app.modules.orders.events import add_event


class Conflict(ApiError):
    """Reminder limit reached"""

    status, code = 409, "expedite_limit"


REASON = "expedite"
COOLDOWN = timedelta(minutes=30)
MAX_PER_ORDER = 3


async def count(db: AsyncSession, order: Order) -> int:
    return int(
        await db.scalar(
            select(func.count()).where(
                OrderEvent.order_id == order.id,
                OrderEvent.tenant_id == order.tenant_id,
                OrderEvent.reason == REASON,
            )
        )
        or 0
    )


async def expedite(
    db: AsyncSession, ref: str, user: User, ip: str | None
) -> tuple[Order, Message | None]:
    order = await service.get_owned(db, ref, user, lock=True)  # row lock serializes the checks
    if order.delivery_mode != "manual" or order.status != "paid":
        raise InvalidState("Only paid orders awaiting manual delivery can be expedited")
    n, last = (
        await db.execute(
            select(func.count(), func.max(OrderEvent.created_at)).where(
                OrderEvent.order_id == order.id,
                OrderEvent.tenant_id == order.tenant_id,
                OrderEvent.reason == REASON,
            )
        )
    ).one()
    if n >= MAX_PER_ORDER:
        raise Conflict("The seller has already been reminded the maximum number of times")
    now = datetime.now(timezone.utc)
    if last is not None and now - last < COOLDOWN:
        raise RateLimited(int((COOLDOWN - (now - last)).total_seconds()) + 1)
    await add_event(db, order, order.status, order.status, actor_id=user.id, reason=REASON)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.expedite_requested",
        actor_id=user.id,
        actor_role=user.role,
        target=f"order:{order.id}",
        ip=ip,
        detail={"count": int(n) + 1},
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="delivery_expedite",
        order_id=str(order.id),
        order_no=order.order_no,
    )
    return order, msg


def alert_staff(res: Resources, order: Order, count_hint: str = "") -> None:
    """Telegram, once per nudge. Plain attributes only: safe after commit."""
    notify_tg.order_event(
        res,
        order,
        "order_paid",
        ["Buyer asks to ship this manual order now"],
        once_suffix=f":expedite:{count_hint}",
        label="URGENT: buyer reminder",
    )
