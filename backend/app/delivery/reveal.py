"""Internal reveal API (never routed by Caddy). Decrypts delivered codes for their owner."""

from __future__ import annotations

import hmac
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.core.audit import audit
from app.core.cardvault import CardContext, VaultError
from app.core.db import set_tenant, tenant_session
from app.core.errors import error_body
from app.core.ratelimit import hit
from app.delivery.context import DeliveryContext
from app.models import CardStock, Delivery, Order

log = logging.getLogger("app.delivery.reveal")

REVEALABLE = frozenset({"delivered", "completed"})


class RevealIn(BaseModel):
    order_id: uuid.UUID
    buyer_id: uuid.UUID


def _err(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


async def _breaker_allows(ctx: DeliveryContext, n: int) -> bool:
    """Global decrypt budget per minute. Exceeding it refuses ALL reveals for the window."""
    key = f"reveal:decrypts:{int(time.time() // 60)}"
    async with ctx.redis.pipeline(transaction=True) as pipe:
        pipe.incrby(key, n)
        pipe.expire(key, 120)
        count, _ = await pipe.execute()
    return int(count) <= ctx.settings.reveal_max_per_min


def create_delivery_app(ctx: DeliveryContext) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/reveal")
    async def reveal(body: RevealIn, request: Request) -> JSONResponse:
        expected = ctx.settings.internal_token
        given = request.headers.get("x-internal-token", "")
        if len(expected) < 32 or not hmac.compare_digest(given.encode(), expected.encode()):
            log.warning("reveal: bad internal token")
            return _err(401, "unauthorized", "Bad internal token")

        async with tenant_session(ctx.maker, None) as db:
            order = await db.scalar(select(Order).where(Order.id == body.order_id))
            if order is None or order.buyer_id != body.buyer_id:
                return _err(404, "not_found", "Order not found")
            tenant_id = order.tenant_id
            await set_tenant(db, tenant_id)
            if order.delivery_mode != "auto_card" or order.status not in REVEALABLE:
                log.warning("reveal refused: order %s in state %s", order.id, order.status)
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="reveal.refused_state",
                    actor_id=body.buyer_id,
                    actor_role="buyer",
                    target=f"order:{order.id}",
                    detail={"status": order.status},
                    severity="warning",
                )
                await db.commit()
                return _err(409, "invalid_state", "Nothing to reveal for this order")

            allowed, reset = await hit(
                ctx.redis,
                "reveal:order",
                str(order.id),
                ctx.settings.reveal_per_order_per_hour,
                3600,
            )
            if not allowed:
                return _err(
                    429,
                    "rate_limited",
                    "Too many reveals for this order",
                    {"Retry-After": str(reset)},
                )

            rows = (
                await db.execute(
                    select(CardStock.id, CardStock.product_id, CardStock.blob)
                    .join(Delivery, Delivery.card_stock_id == CardStock.id)
                    .where(Delivery.order_id == order.id, CardStock.order_id == order.id)
                    .order_by(Delivery.created_at, CardStock.id)
                )
            ).all()
            if not rows:
                return _err(409, "invalid_state", "Nothing to reveal for this order")

            if not await _breaker_allows(ctx, len(rows)):
                log.critical("reveal circuit breaker OPEN: global decrypt budget exceeded")
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="reveal.circuit_open",
                    actor_id=body.buyer_id,
                    actor_role="buyer",
                    target=f"order:{order.id}",
                    severity="critical",
                )
                await db.commit()
                return _err(503, "unavailable", "Reveal temporarily unavailable")

            secrets: list[str] = []
            try:
                for card_id, product_id, blob in rows:
                    ctx_ = CardContext(str(tenant_id), str(product_id), str(card_id))
                    secrets.append(ctx.opener.open(bytes(blob), ctx_))
            except VaultError:
                log.critical("vault open failed for order %s (tamper or key mismatch)", order.id)
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="reveal.vault_error",
                    target=f"order:{order.id}",
                    severity="critical",
                )
                await db.commit()
                return _err(503, "unavailable", "Reveal temporarily unavailable")

            await db.execute(
                update(Delivery)
                .where(Delivery.order_id == order.id)
                .values(
                    reveal_count=Delivery.reveal_count + 1,
                    first_revealed_at=func.coalesce(Delivery.first_revealed_at, func.now()),
                )
            )
            await audit(
                db,
                tenant_id=tenant_id,
                action="order.reveal",
                actor_id=body.buyer_id,
                actor_role="buyer",
                target=f"order:{order.id}",
                detail={"cards": len(secrets)},
            )
            await db.commit()
        return JSONResponse({"secrets": secrets}, headers={"Cache-Control": "no-store"})

    return app
