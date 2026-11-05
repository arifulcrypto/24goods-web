"""JSON views matching docs/api-contract.md. Pure functions over model rows so both the
API and the delivery service emit identical shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.i18n import pick
from app.core.money import fmt
from app.models import AfterSales, Attachment, Message, Order, Product, User
from app.modules.common.sitedefaults import resolve_after_sales_hours

REVEALABLE = frozenset({"delivered", "completed"})


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def file_url(attachment_id: object) -> str:
    return f"/api/v1/files/{attachment_id}"


def attachment_view(a: Attachment | None) -> dict[str, Any] | None:
    if a is None:
        return None
    out: dict[str, Any] = {
        "id": str(a.id),
        "url": file_url(a.id),
        "mime": a.mime,
        "bytes": a.bytes,
        "kind": a.kind,
    }
    if a.name:
        out["name"] = a.name
    if a.width is not None:
        out["width"] = a.width
    if a.height is not None:
        out["height"] = a.height
    if a.duration_ms is not None:
        out["duration_ms"] = a.duration_ms
    return out


def user_view(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "locale": u.locale,
        "is_instant": u.is_instant,
        "handle": u.handle,
    }


def product_summary(
    p: Product, lang: str, stock: int | None, tenant_settings: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "title": pick(p.title_i18n, lang),
        "cover": p.images[0] if p.images else None,
        "price": fmt(p.price_minor),
        "currency": p.currency,
        "delivery_mode": p.delivery_mode,
        "stock": stock if p.delivery_mode == "auto_card" else None,
        "category": p.category,
        "sold": p.sold,
        "after_sales_hours": resolve_after_sales_hours(p.after_sales_hours, tenant_settings),
    }


def product_view(
    p: Product, lang: str, stock: int | None, tenant_settings: dict[str, Any] | None = None
) -> dict[str, Any]:
    out = product_summary(p, lang, stock, tenant_settings)
    out.update(
        description=pick(p.description_i18n, lang),
        images=list(p.images),
        status=p.status,
        delivery_format=p.delivery_format,
        delivery_separator=p.delivery_separator,
        redeem_url=p.redeem_url,
        usage_html=pick(p.usage_html_i18n, lang),
    )
    return out


def format_labels(p: Product | None) -> list[str]:
    if p is None or not p.delivery_format:
        return []
    return [part.strip() for part in p.delivery_format.split(p.delivery_separator or "----")]


def delivery_info(p: Product | None, lang: str) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "format_labels": format_labels(p),
        "separator": p.delivery_separator,
        "redeem_url": p.redeem_url,
        "usage_html": pick(p.usage_html_i18n, lang),
    }


def admin_product_view(p: Product, stock: int | None) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "cover": p.images[0] if p.images else None,
        "price": fmt(p.price_minor),
        "currency": p.currency,
        "delivery_mode": p.delivery_mode,
        "stock": stock if p.delivery_mode == "auto_card" else None,
        "category": p.category,
        "sold": p.sold,
        "images": list(p.images),
        "status": p.status,
        "title_i18n": {"en": p.title_i18n.get("en", ""), "zh-CN": p.title_i18n.get("zh-CN", "")},
        "description_i18n": {
            "en": p.description_i18n.get("en", ""),
            "zh-CN": p.description_i18n.get("zh-CN", ""),
        },
        "floor_price": fmt(p.floor_price_minor),
        "created_at": iso(p.created_at),
        "delivery_format": p.delivery_format,
        "delivery_separator": p.delivery_separator,
        "redeem_url": p.redeem_url,
        "usage_i18n": {"en": p.usage_i18n.get("en", ""), "zh-CN": p.usage_i18n.get("zh-CN", "")},
        "after_sales_hours": p.after_sales_hours,
        "group_sort": p.group_sort,
    }


def product_card_payload(p: Product, lang: str) -> dict[str, Any]:
    return {
        "product_id": str(p.id),
        "title": pick(p.title_i18n, lang),
        "cover": p.images[0] if p.images else None,
        "price": fmt(p.price_minor),
        "currency": p.currency,
    }


def order_card_payload(o: Order) -> dict[str, Any]:
    return {
        "order_id": str(o.id),
        "title": o.title,
        "amount": fmt(o.amount_minor),
        "currency": o.currency,
        "status": o.status,
    }


def after_sales_view(
    t: AfterSales, attachments: list[Attachment] | None = None, order_no: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(t.id),
        "order_id": str(t.order_id),
        "reason": t.reason,
        "description": t.description,
        "attachments": [attachment_view(a) for a in (attachments or [])],
        "status": t.status,
        "resolution_note": t.resolution_note,
        "created_at": iso(t.created_at),
        "updated_at": iso(t.updated_at),
        "resolved_at": iso(t.resolved_at),
    }
    if order_no is not None:
        out["order_no"] = order_no
    return out


def order_view(
    o: Order,
    *,
    viewer_is_owner: bool,
    attachment: Attachment | None = None,
    product: Product | None = None,
    ticket: dict[str, Any] | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    manual = None
    if o.delivery_mode == "manual" and (o.manual_delivery_text or attachment is not None):
        manual = {"text": o.manual_delivery_text, "attachment": attachment_view(attachment)}
    return {
        "id": str(o.id),
        "order_no": o.order_no,
        "title": o.title,
        "product_id": str(o.product_id) if o.product_id else None,
        "quote_id": str(o.quote_id) if o.quote_id else None,
        "qty": o.qty,
        "amount": fmt(o.amount_minor),
        "currency": o.currency,
        "status": o.status,
        "delivery_mode": o.delivery_mode,
        "channel": o.channel,
        "created_at": iso(o.created_at),
        "paid_at": iso(o.paid_at),
        "delivered_at": iso(o.delivered_at),
        "expires_at": iso(o.expires_at),
        "version": o.version,
        "after_sales_until": iso(o.after_sales_until),
        "auto_confirm_at": iso(o.auto_confirm_at),
        "after_sales": ticket,
        "delivery_info": delivery_info(product, lang),
        "manual_delivery": manual,
        "can_reveal": bool(
            viewer_is_owner and o.delivery_mode == "auto_card" and o.status in REVEALABLE
        ),
    }


SYSTEM_NAME = "System"


def message_view(
    m: Message,
    *,
    sender_name: str | None,
    attachment: Attachment | None = None,
    payload_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(m.payload) if m.payload is not None else None
    if payload is not None and payload_overlay:
        payload.update(payload_overlay)
    return {
        "id": m.id,
        "conversation_id": str(m.conversation_id),
        "client_msg_id": m.client_msg_id,
        "sender": {
            "id": str(m.sender_id) if m.sender_id else None,
            "role": m.sender_role,
            "display_name": sender_name or SYSTEM_NAME,
        },
        "type": m.type,
        "text": m.text,
        "attachment": attachment_view(attachment),
        "payload": payload,
        "created_at": iso(m.created_at),
    }
