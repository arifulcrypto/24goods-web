"""Internal reveal API (never routed by Caddy). Decrypts delivered codes for their owner."""

from __future__ import annotations

import hmac
import json
import logging
import uuid

from sqlalchemy import func, select, update
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.audit import audit
from app.core.cardvault import CardContext, VaultError
from app.core.db import set_tenant, tenant_session
from app.core.errors import error_body
from app.core.ratelimit import hit, release, reserve
from app.delivery.context import DeliveryContext
from app.models import CardStock, Delivery, Order

log = logging.getLogger("app.delivery.reveal")

REVEALABLE = frozenset({"delivered", "completed"})


MAX_BODY = 4096


class BadRequest(ValueError):
    pass


async def _parse(request: Request) -> tuple[uuid.UUID, uuid.UUID]:
    """``{order_id, buyer_id}`` (UUID strings). Small, strict, no extra fields needed."""
    body = await request.body()
    if len(body) > MAX_BODY:
        raise BadRequest("body too large")
    try:
        data = json.loads(body)
        return uuid.UUID(str(data["order_id"])), uuid.UUID(str(data["buyer_id"]))
    except (ValueError, KeyError, TypeError):
        raise BadRequest("order_id and buyer_id (UUID) are required") from None


def _err(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


GLOBAL_WINDOW_S = 60


async def _reserve_budgets(ctx: DeliveryContext, buyer_id: uuid.UUID, n: int) -> str | None:
    """Reserve ``n`` decrypts against the per-buyer and the global budgets.

    Both reservations are check-then-increment in one Lua call: a refused request never
    inflates a counter, so one buyer hammering reveal cannot exhaust (trip) the global
    breaker for everyone. Returns None if allowed, else "buyer" or "global".
    """
    s = ctx.settings
    ok, _ = await reserve(
        ctx.redis,
        "reveal:buyer:d",
        str(buyer_id),
        n,
        s.reveal_per_buyer_cards,
        s.reveal_per_buyer_window_s,
    )
    if not ok:
        return "buyer"
    ok, _ = await reserve(
        ctx.redis, "reveal:global", "all", n, s.reveal_max_per_min, GLOBAL_WINDOW_S
    )
    if not ok:
        await release(ctx.redis, "reveal:buyer:d", str(buyer_id), n, s.reveal_per_buyer_window_s)
        return "global"
    return None


def create_delivery_app(ctx: DeliveryContext) -> Starlette:
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def reveal(request: Request) -> JSONResponse:
        expected = ctx.settings.internal_token
        given = request.headers.get("x-internal-token", "")
        if len(expected) < 32 or not hmac.compare_digest(given.encode(), expected.encode()):
            log.warning("reveal: bad internal token")
            return _err(401, "unauthorized", "Bad internal token")
        try:
            order_id, buyer_id = await _parse(request)
        except BadRequest as exc:
            return _err(422, "validation", str(exc))

        async with tenant_session(ctx.maker, None) as db:
            order = await db.scalar(select(Order).where(Order.id == order_id))
            if order is None or order.buyer_id != buyer_id:
                return _err(404, "not_found", "Order not found")
            tenant_id = order.tenant_id
            await set_tenant(db, tenant_id)
            if order.delivery_mode != "auto_card" or order.status not in REVEALABLE:
                log.warning("reveal refused: order %s in state %s", order.id, order.status)
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="reveal.refused_state",
                    actor_id=buyer_id,
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

            refused = await _reserve_budgets(ctx, buyer_id, len(rows))
            if refused == "buyer":
                return _err(
                    429,
                    "rate_limited",
                    "Too many codes revealed recently",
                    {"Retry-After": str(ctx.settings.reveal_per_buyer_window_s)},
                )
            if refused == "global":
                log.critical("reveal circuit breaker OPEN: global decrypt budget exceeded")
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="reveal.circuit_open",
                    actor_id=buyer_id,
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
                actor_id=buyer_id,
                actor_role="buyer",
                target=f"order:{order.id}",
                detail={"cards": len(secrets)},
            )
            await db.commit()
        return JSONResponse({"secrets": secrets}, headers={"Cache-Control": "no-store"})

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/internal/reveal", reveal, methods=["POST"]),
        ]
    )
    app.state.ctx = ctx
    return app
