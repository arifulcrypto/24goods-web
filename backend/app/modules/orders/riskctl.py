"""Order-creation risk control (#58 §6): per-account and per-IP throttles on every path that
creates an order (buy-now, cart checkout, quote acceptance), with an audited flood signal.

Limits come from settings so operators can tune them without a release. The per-IP limit is
deliberately looser than the per-account one: many buyers share an address behind carrier NAT.
The unpaid-order cap (``max_pending_orders``) stays the main brake; this stops scripted bursts.
"""

from __future__ import annotations

from app.core.audit import audit
from app.core.errors import RateLimited
from app.core.ratelimit import hit
from app.core.resources import Resources
from app.models import User


async def guard_order_create(res: Resources, db, user: User, ip: str | None) -> None:  # type: ignore[no-untyped-def]
    s = res.settings
    checks = [("order:create", str(user.id), s.order_create_per_user, s.order_create_user_window_s)]
    if ip:
        checks.append(("order:create:ip", ip, s.order_create_per_ip, s.order_create_ip_window_s))
    for name, ident, limit, window in checks:
        allowed, reset = await hit(res.redis, name, ident, limit, window)
        if allowed:
            continue
        # one high-severity audit row per identity and window, not one per refused request
        if await res.redis.set(f"risk:order_flood:{name}:{ident}", "1", nx=True, ex=window):
            await audit(
                db,
                tenant_id=user.tenant_id,
                action="risk.order_flood_detected",
                actor_id=user.id,
                actor_role=user.role,
                target=f"user:{user.id}",
                ip=ip,
                detail={"limit": limit, "window_s": window, "scope": name.rsplit(":", 1)[-1]},
                severity="high",
            )
            await db.commit()
        raise RateLimited(retry_after=reset)
