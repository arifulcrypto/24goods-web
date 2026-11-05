"""Order state-change records (shared by the API and the delivery service)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, OrderEvent


async def add_event(
    db: AsyncSession,
    order: Order,
    from_status: str | None,
    to_status: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_kind: str = "user",
    reason: str | None = None,
) -> None:
    db.add(
        OrderEvent(
            tenant_id=order.tenant_id,
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            actor_kind=actor_kind,
            reason=reason,
        )
    )


def snapshot(o: Order) -> dict[str, Any]:
    return {"status": o.status, "amount_minor": o.amount_minor, "version": o.version}
