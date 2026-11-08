"""Private EPay payment presentation. A browser view never settles an order."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user, get_db
from app.core.errors import NotFound
from app.core.money import fmt
from app.models import Checkout, Order, PaymentAttempt, User
from app.modules.payments.usdt import qr_svg

router = APIRouter(prefix="/pay/epay", tags=["payments"])


@router.get("/{no}")
async def payment_view(
    no: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    model, col = (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
    target = await db.scalar(
        select(model).where(col == no, model.tenant_id == user.tenant_id, model.buyer_id == user.id)
    )
    if target is None:
        raise NotFound("Payment not found")
    att = await db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.tenant_id == user.tenant_id,
            PaymentAttempt.local_ref == no,
            PaymentAttempt.provider_id.is_not(None),
        )
        .order_by(PaymentAttempt.created_at.desc())
        .limit(1)
    )
    if att is None:
        raise NotFound("Payment not found")
    from datetime import datetime, timezone

    expired = att.expires_at is None or att.expires_at <= datetime.now(timezone.utc)
    usable = target.status == "pending_payment" and not expired and att.status == "ready"
    data = att.data if usable else {}
    return {
        "attempt_id": str(att.id),
        "status": target.status,
        "attempt_status": att.status,
        "amount": fmt(att.amount_minor),
        "currency": att.currency,
        "expires_at": att.expires_at.isoformat() if att.expires_at else None,
        "expired": expired,
        "kind": data.get("kind"),
        "redirect_host": urlsplit(data.get("payload", "")).hostname
        if data.get("kind") == "redirect"
        else None,
        "redirect_url": data.get("payload") if data.get("kind") == "redirect" else None,
        "qr_svg": qr_svg(data["payload"]) if data.get("kind") == "qr" else None,
    }
