"""Telegram staff notifications (review-v5.2 §10).

Best effort, off the request path: callers use :func:`spawn` (a tracked asyncio task) and
:func:`notify` never raises. The bot token is the ``telegram_bot_token`` tenant secret; it is
read only here, sent only to api.telegram.org and never logged (failures log the exception
class, not the message, because httpx messages can carry the URL). Messages carry an event
label, order no, product title, amount and an admin link: never card secrets or buyer
contact data. Dedupe/throttle keys are set only once a message is actually going out.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from collections.abc import Coroutine
from typing import Any

from app.core import secretbox
from app.core.db import tenant_session
from app.core.money import fmt
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.modules.content.site import tenant_settings

log = logging.getLogger("app.notify_tg")

EVENTS = (
    "order_paid",
    "chat_message",
    "after_sales",
    "payment_review",
    "reseller_application",
    "low_stock",
)
API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_S = 5.0
LOW_STOCK_BELOW = 3
LABELS = {
    "order_paid": "Order paid",
    "chat_message": "New buyer message",
    "after_sales": "After-sales request",
    "payment_review": "Payment needs review",
    "reseller_application": "Reseller application",
    "low_stock": "Low card stock",
}
_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> None:
    """Run ``coro`` in the background; the task is held so it is not garbage collected."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no loop (should not happen in the API): drop quietly
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Wait for in-flight notifications (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def mask_email(email: str) -> str:
    """``alice@example.com`` -> ``a***@example.com`` (never the full address)."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def admin_link(res: Resources, path: str) -> str:
    return f"{res.settings.public_base_url.rstrip('/')}/admin/{path.lstrip('/')}"


def build_text(event: str, lines: list[str], link: str | None = None) -> str:
    """HTML message: bold label, escaped body lines, optional link."""
    parts = [f"<b>{html.escape(LABELS.get(event, event))}</b>", *(html.escape(x) for x in lines)]
    if link:
        parts.append(html.escape(link))
    return "\n".join(parts)


async def _post(res: Resources, token: str, chat_id: str, text: str) -> bool:
    try:
        r = await res.http.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            log.warning("telegram send failed: http %s", r.status_code)
            return False
        return True
    except Exception as exc:  # network, timeout, TLS ...  never log str(exc) (may hold the URL)
        log.warning("telegram send failed: %s", type(exc).__name__)
        return False


async def notify(
    res: Resources,
    tenant_id: uuid.UUID,
    event: str,
    text: str,
    *,
    once: tuple[str, int] | None = None,
) -> bool:
    """Send ``text`` to every configured chat when enabled for ``event``. Never raises.

    ``once=(key, ttl_s)``: Redis SET NX EX dedupe/throttle (only claimed when a send follows).
    """
    try:
        async with tenant_session(res.maker, tenant_id) as db:
            cfg = section(await tenant_settings(db, tenant_id), "notifications")
            if not cfg["telegram_enabled"] or not cfg.get(f"event_{event}", False):
                return False
            chats = cfg["telegram_chat_ids"]
            token = await secretbox.get_secret(db, res.settings, tenant_id, "telegram_bot_token")
        if not token or not chats:
            return False
        if once is not None:
            key, ttl = once
            if not await res.redis.set(f"tg:{tenant_id}:{key}", "1", nx=True, ex=ttl):
                return False
        results = [await _post(res, token, c, text) for c in chats]
        return any(results)
    except Exception as exc:
        log.warning("telegram notify failed: %s", type(exc).__name__)
        return False


def notify_bg(
    res: Resources,
    tenant_id: uuid.UUID,
    event: str,
    lines: list[str],
    *,
    link: str | None = None,
    once: tuple[str, int] | None = None,
) -> None:
    spawn(notify(res, tenant_id, event, build_text(event, lines, link), once=once))


async def send_test(res: Resources, tenant_id: uuid.UUID) -> tuple[bool, str | None]:
    """Admin test: ignores the enable/event toggles, reports a short error code. Never raises."""
    try:
        async with tenant_session(res.maker, tenant_id) as db:
            cfg = section(await tenant_settings(db, tenant_id), "notifications")
            token = await secretbox.get_secret(db, res.settings, tenant_id, "telegram_bot_token")
    except Exception as exc:  # e.g. unseal failure after key rotation
        log.warning("telegram test: config unavailable (%s)", type(exc).__name__)
        return False, "config_error"
    if not token:
        return False, "no_token"
    if not cfg["telegram_chat_ids"]:
        return False, "no_chat"
    text = "<b>Test</b>\nTest message from your store settings."
    ok = [await _post(res, token, c, text) for c in cfg["telegram_chat_ids"]]
    return (True, None) if all(ok) else (False, "send_failed")


# ------------------------------------------------------------------ event helpers
def order_lines(order: Any) -> list[str]:
    return [
        f"Order {order.order_no}",
        f"{order.title}",
        f"{fmt(order.amount_minor)} {order.currency}",
    ]


def order_event(res: Resources, order: Any, event: str, extra: list[str] | None = None) -> None:
    """Notify about one order (plain attributes only: safe to call after commit)."""
    notify_bg(
        res,
        order.tenant_id,
        event,
        [*order_lines(order), *(extra or [])],
        link=admin_link(res, "orders"),
        once=(f"{event}:{order.id}", 7 * 86400),
    )


def outcome_events(res: Resources, orders: list[Any]) -> None:
    """After a settled payment: paid -> order_paid (+ low stock); manual_review -> review.
    Never raises: runs inline on the payment-notify path after commit."""
    try:
        _outcome_events(res, orders)
    except Exception as exc:  # a notification bug must not fail a notify reply
        log.warning("telegram outcome events skipped (%s)", type(exc).__name__)


def _outcome_events(res: Resources, orders: list[Any]) -> None:
    for o in orders:
        if o.status == "paid":
            mode = "manual delivery" if o.delivery_mode == "manual" else "auto delivery"
            order_event(res, o, "order_paid", [mode] if o.kind == "goods" else [o.kind])
            if o.delivery_mode != "manual" and o.product_id is not None:
                spawn(low_stock_check(res, o.tenant_id, o.product_id, o.qty, o.title))
        elif o.status == "manual_review":
            order_event(res, o, "payment_review")


async def low_stock_check(
    res: Resources, tenant_id: uuid.UUID, product_id: uuid.UUID, qty: int, title: str
) -> None:
    """Throttled per product (1 h) once available cards, minus this order's, drop below 3."""
    try:
        from sqlalchemy import func, select

        from app.models import CardStock

        async with tenant_session(res.maker, tenant_id) as db:
            avail = await db.scalar(
                select(func.count())
                .select_from(CardStock)
                .where(
                    CardStock.tenant_id == tenant_id,
                    CardStock.product_id == product_id,
                    CardStock.status == "available",
                )
            )
        left = int(avail or 0) - qty
        if left >= LOW_STOCK_BELOW:
            return
        await notify(
            res,
            tenant_id,
            "low_stock",
            build_text(
                "low_stock",
                [title, f"Available after this order: {max(left, 0)}"],
                admin_link(res, "inventory"),
            ),
            once=(f"low_stock:{product_id}", 3600),
        )
    except Exception as exc:
        log.warning("low stock check failed: %s", type(exc).__name__)
