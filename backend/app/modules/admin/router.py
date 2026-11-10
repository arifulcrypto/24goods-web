"""Admin / agent console API (``/api/v1/admin/*``)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apitoken import require_scope
from app.core.audit import audit
from app.core.deps import (
    Page,
    client_ip,
    get_db,
    get_lang,
    get_page,
    get_res,
    require_admin,
    require_staff,
)
from app.core.errors import Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.admin import products as product_svc
from app.modules.admin import service
from app.modules.admin.schemas import (
    AgentStatusIn,
    BulkProductsIn,
    CardsImportIn,
    ProductIn,
    ProductPatch,
    ReplyTemplateIn,
)
from app.modules.aftersales.schemas import RefundIn
from app.modules.catalog.service import support_status
from app.modules.chat import quotes as quote_svc
from app.modules.chat import service as chat
from app.modules.chat import templates as reply_tpl
from app.modules.chat.schemas import AssignIn, MessageIn, QuoteIn, ReadIn
from app.modules.common.views import file_url
from app.modules.inventory import service as inventory
from app.modules.orders import admin as order_admin
from app.modules.orders.schemas import CancelIn, DeliverIn, PriceIn, ReleaseHoldIn
from app.modules.orders.service import broadcast_order
from app.modules.payments.channels import LABELS as CHANNEL_LABELS
from app.modules.realtime.publish import publish_agent_status
from app.modules.uploads.service import image_from_upload, store_attachment
from app.modules.uploads.storage import FileStore

router = APIRouter(prefix="/admin", tags=["admin"])

OrderStatus = Literal[
    "pending_payment",
    "paid",
    "delivered",
    "completed",
    "cancelled",
    "expired",
    "refunded",
    "manual_review",
]


# ---------------------------------------------------------------- dashboard
@router.get("/stats", dependencies=[Depends(require_scope("stats"))])
async def stats(
    _: User = Depends(require_staff),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.stats(db, lang)


# ---------------------------------------------------------------- products
@router.get("/products", dependencies=[Depends(require_scope("products:read"))])
async def list_products(
    q: str | None = Query(None, max_length=100),
    status: Literal["active", "draft", "archived"] | None = None,
    page: Page = Depends(get_page),
    _: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await product_svc.list_products(db, page, q, status)


@router.get("/products/{product_id}", dependencies=[Depends(require_scope("products:read"))])
async def get_product(
    product_id: uuid.UUID, _: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> dict:
    return await product_svc.view(db, await product_svc.get(db, product_id))


@router.post("/products", dependencies=[Depends(require_scope("products:write"))])
async def create_product(
    body: ProductIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    p = await product_svc.create(db, body, admin, client_ip(request))
    return await product_svc.view(db, p)


@router.patch("/products/{product_id}", dependencies=[Depends(require_scope("products:write"))])
async def patch_product(
    product_id: uuid.UUID,
    body: ProductPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    p = await product_svc.patch(db, product_id, body, admin, client_ip(request))
    return await product_svc.view(db, p)


@router.post("/products/bulk", dependencies=[Depends(require_scope("products:write"))])
async def bulk_products(
    body: BulkProductsIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return {"updated": await product_svc.bulk(db, body, admin, client_ip(request))}


@router.delete(
    "/products/{product_id}",
    status_code=204,
    dependencies=[Depends(require_scope("products:write"))],
)
async def delete_product(
    product_id: uuid.UUID,
    request: Request,
    hard: bool = False,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await product_svc.archive(db, product_id, admin, client_ip(request), hard=hard)
    return Response(status_code=204)


@router.post(
    "/products/{product_id}/restore", dependencies=[Depends(require_scope("products:write"))]
)
async def restore_product(
    product_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    p = await product_svc.restore(db, product_id, admin, client_ip(request))
    return await product_svc.view(db, p)


@router.post(
    "/products/{product_id}/images", dependencies=[Depends(require_scope("products:write"))]
)
async def upload_product_image(
    product_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    p = await product_svc.get(db, product_id)
    processed = await image_from_upload(file)
    att = await store_attachment(
        db,
        FileStore(res.settings.upload_dir),
        tenant_id=admin.tenant_id,
        owner_id=admin.id,
        kind="image",
        processed=processed,
        purpose="product",
        is_public=True,
    )
    url = file_url(att.id)
    if len(p.images) < 10:
        p.images = [*p.images, url]
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="product.image_added",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"product:{p.id}",
        ip=client_ip(request),
        detail={"attachment_id": str(att.id)},
    )
    await db.commit()
    return {"url": url}


@router.post(
    "/products/{product_id}/cards/import", dependencies=[Depends(require_scope("cards:import"))]
)
async def import_cards(
    product_id: uuid.UUID,
    body: CardsImportIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "cards:import", str(admin.id), 30, 3600)
    p = await product_svc.get(db, product_id)
    result = await inventory.import_cards(
        db,
        res.sealer,
        tenant_id=admin.tenant_id,
        product=p,
        lines=body.lines,
        actor_id=admin.id,
        actor_role=admin.role,
        ip=client_ip(request),
        note=body.note,
        expires_at=body.expires_at,
    )
    return {
        "imported": result.imported,
        "duplicates": result.duplicates,
        "rejected": result.rejected,
        "batch_id": str(result.batch_id) if result.batch_id else None,
    }


@router.get("/products/{product_id}/cards")
async def list_cards(
    product_id: uuid.UUID,
    status: Literal["available", "reserved", "sold", "void"] | None = None,
    batch_id: uuid.UUID | None = None,
    expiring_days: int | None = Query(None, ge=0, le=3650),
    order_no: str | None = Query(None, max_length=16),
    page: Page = Depends(get_page),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await inventory.list_cards(
        db,
        product_id,
        status,
        page,
        batch_id=batch_id,
        expiring_days=expiring_days,
        order_no=order_no,
    )


# ---------------------------------------------------------------- orders
@router.get("/orders", dependencies=[Depends(require_scope("orders:read"))])
async def list_orders(
    status: OrderStatus | None = None,
    q: str | None = Query(None, max_length=100),
    buyer_id: uuid.UUID | None = None,
    reseller_id: uuid.UUID | None = None,
    overdue_manual: bool = False,
    channel: str | None = Query(None, max_length=32),
    page: Page = Depends(get_page),
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if channel is not None and channel != "none" and channel not in CHANNEL_LABELS:
        raise Validation("Unknown payment channel")
    return await order_admin.list_orders(
        db,
        page,
        status,
        q,
        staff.tenant_id,
        buyer_id,
        reseller_id=reseller_id,
        overdue_manual=overdue_manual,
        channel=channel,
    )


@router.get("/orders/{order_id}", dependencies=[Depends(require_scope("orders:read"))])
async def order_detail(
    order_id: uuid.UUID, staff: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> dict:
    return await order_admin.order_detail(db, order_id, staff.tenant_id)


async def _order_reply(res: Resources, db: AsyncSession, order, msg) -> dict:  # type: ignore[no-untyped-def]
    await broadcast_order(res, db, order, [msg])
    (view,) = await order_admin.admin_views(db, [order])
    return view


@router.patch("/orders/{order_id}/price")
async def change_price(
    order_id: uuid.UUID,
    body: PriceIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await order_admin.change_price(
        db,
        order_id,
        body.amount,
        body.reason,
        staff,
        client_ip(request),
        remove_markup=body.remove_markup,
    )
    return await _order_reply(res, db, order, msg)


@router.post("/orders/{order_id}/deliver")
async def deliver(
    order_id: uuid.UUID,
    body: DeliverIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await order_admin.deliver_manual(
        db, order_id, body.text, body.attachment_id, staff, client_ip(request)
    )
    return await _order_reply(res, db, order, msg)


@router.post("/orders/{order_id}/requeue-delivery")
async def requeue_delivery(
    order_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await order_admin.requeue_delivery(db, order_id, admin, client_ip(request))
    return await _order_reply(res, db, order, msg)


@router.post("/orders/{order_id}/release-hold")
async def release_hold(
    order_id: uuid.UUID,
    body: ReleaseHoldIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """#72 B2: accept the recorded payment of a held order, or close it when nothing was paid."""
    order, msg = await order_admin.release_hold(
        db, res, order_id, body.outcome, body.reason, admin, client_ip(request)
    )
    if body.outcome == "close":  # already broadcast inside, like cancel
        (view,) = await order_admin.admin_views(db, [order])
        return view
    return await _order_reply(res, db, order, msg)


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: uuid.UUID,
    body: RefundIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await order_admin.refund_undelivered(
        db, order_id, body.reference, body.note, admin, client_ip(request)
    )
    return await _order_reply(res, db, order, msg)


@router.post("/orders/{order_id}/cancel")
async def cancel(
    order_id: uuid.UUID,
    body: CancelIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order = await order_admin.cancel(db, res, order_id, body.reason, staff, client_ip(request))
    (view,) = await order_admin.admin_views(db, [order])  # cancel_group already broadcast
    return view


# ---------------------------------------------------------------- conversations
@router.get("/conversations", dependencies=[Depends(require_scope("chat"))])
async def list_conversations(
    filter: Literal["all", "mine", "unassigned"] = "all",
    status: Literal["open", "closed"] | None = None,
    page: Page = Depends(get_page),
    staff: User = Depends(require_staff),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.list_conversations(db, staff, page, filter, status, lang)


@router.get("/conversations/{conv_id}/messages", dependencies=[Depends(require_scope("chat"))])
async def conversation_messages(
    conv_id: uuid.UUID,
    after_id: int | None = Query(None, ge=0),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    _: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    conv = await chat.staff_conversation(db, conv_id)
    return await chat.list_messages(db, conv, after_id, before_id, limit)


@router.post("/conversations/{conv_id}/messages", dependencies=[Depends(require_scope("chat"))])
async def send_message(
    conv_id: uuid.UUID,
    body: MessageIn,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    conv = await chat.staff_conversation(db, conv_id)
    await enforce(res.redis, "msg:user", str(staff.id), 60, 60)
    msg, created = await chat.send_user_message(
        db,
        conv,
        staff,
        role="agent",
        type=body.type,
        text=body.text,
        attachment_id=body.attachment_id,
        client_msg_id=body.client_msg_id,
        store=FileStore(res.settings.upload_dir),
        product_id=body.product_id,
    )
    if created:
        await chat.broadcast(res, db, conv, [msg])
    (view,) = await chat.message_views(db, [msg])
    return view


@router.post("/content/images", dependencies=[Depends(require_scope("uploads"))])
async def upload_content_image(
    request: Request,
    file: UploadFile = File(...),
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Public image for markdown content (product descriptions, articles, pages, announcement).
    Same hostile-upload pipeline as every image: magic bytes, re-encode, EXIF strip, size cap."""
    await enforce(res.redis, "content:image", str(staff.id), 60, 3600)
    processed = await image_from_upload(file)
    att = await store_attachment(
        db,
        FileStore(res.settings.upload_dir),
        tenant_id=staff.tenant_id,
        owner_id=staff.id,
        kind="image",
        processed=processed,
        purpose="product",
        is_public=True,
    )
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="content.image_uploaded",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"attachment:{att.id}",
        ip=client_ip(request),
    )
    await db.commit()
    return {"id": str(att.id), "url": file_url(att.id)}


@router.post("/conversations/{conv_id}/read", status_code=204)
async def mark_read(
    conv_id: uuid.UUID,
    body: ReadIn,
    _: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conv = await chat.staff_conversation(db, conv_id)
    await chat.mark_read(db, conv, body.message_id, "staff")
    return Response(status_code=204)


async def _conversation_reply(
    res: Resources,
    db: AsyncSession,
    conv,  # type: ignore[no-untyped-def]
    msgs: list,
    lang: str,
) -> dict:
    await chat.broadcast(res, db, conv, msgs)
    (view,) = await chat.conversation_views(db, [conv], viewer="staff", lang=lang)
    return view


@router.post("/conversations/{conv_id}/assign")
async def assign(
    conv_id: uuid.UUID,
    body: AssignIn,
    request: Request,
    staff: User = Depends(require_staff),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    conv = await chat.staff_conversation(db, conv_id)
    msgs = await service.assign(db, conv, body.agent_id, staff, client_ip(request))
    return await _conversation_reply(res, db, conv, msgs, lang)


@router.post("/conversations/{conv_id}/close")
async def close(
    conv_id: uuid.UUID,
    request: Request,
    staff: User = Depends(require_staff),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    conv = await chat.staff_conversation(db, conv_id)
    msgs = await service.close(db, conv, staff, client_ip(request))
    return await _conversation_reply(res, db, conv, msgs, lang)


@router.post("/conversations/{conv_id}/quotes")
async def create_quote(
    conv_id: uuid.UUID,
    body: QuoteIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    conv = await chat.staff_conversation(db, conv_id)
    quote, msg = await quote_svc.create_quote(
        db, res.settings, conv, staff, body, client_ip(request)
    )
    await chat.broadcast(res, db, conv, [msg])
    return quote_svc.quote_payload(quote)


@router.post("/quotes/{quote_id}/withdraw")
async def withdraw_quote(
    quote_id: uuid.UUID,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    quote, sysmsg = await quote_svc.withdraw_quote(
        db, res.settings, quote_id, staff, client_ip(request)
    )
    msg = await quote_svc.quote_message(db, quote)  # same id, updated status
    conv = await chat.staff_conversation(db, quote.conversation_id)
    await chat.broadcast(res, db, conv, [m for m in (msg, sysmsg) if m is not None])
    return quote_svc.quote_payload(quote)


# ---------------------------------------------------------------- agents
@router.get("/agent/status")
async def get_agent_status(
    staff: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> dict:
    return await service.get_agent_status(db, staff)


@router.put("/agent/status")
async def put_agent_status(
    body: AgentStatusIn,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    view = await service.set_agent_status(db, staff, body.status, body.message)
    best = await support_status(db)
    await publish_agent_status(
        res.redis, staff.tenant_id, best, {**view, "agent_id": str(staff.id)}
    )
    return view


@router.get("/agents")
async def agents(_: User = Depends(require_staff), db: AsyncSession = Depends(get_db)) -> list:
    return await service.agents(db)


# ---------------------------------------------------------------- canned replies
@router.get("/reply-templates")
async def reply_templates(
    staff: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> list:
    return await reply_tpl.listing(db, staff)


@router.post("/reply-templates", status_code=201)
async def create_reply_template(
    body: ReplyTemplateIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await reply_tpl.create(
        db,
        staff,
        title=body.title,
        body=body.body,
        shared=body.shared,
        ip=client_ip(request),
    )


@router.put("/reply-templates/{ref}")
async def update_reply_template(
    ref: uuid.UUID,
    body: ReplyTemplateIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await reply_tpl.update(
        db, staff, ref, title=body.title, body=body.body, ip=client_ip(request)
    )


@router.delete("/reply-templates/{ref}", status_code=204)
async def delete_reply_template(
    ref: uuid.UUID,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await reply_tpl.remove(db, staff, ref, client_ip(request))
    return Response(status_code=204)


# ---------------------------------------------------------------- finance / audit
@router.get("/ledger")
async def ledger(
    page: Page = Depends(get_page),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.ledger(db, page)


@router.get("/audit")
async def audit_log(
    flt: service.AuditFilter = Depends(service.audit_filter),
    page: Page = Depends(get_page),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.audit_page(db, page, flt)


@router.get("/audit.csv")
async def audit_csv(
    flt: service.AuditFilter = Depends(service.audit_filter),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV of the filtered operation log (newest first, at most 10 000 rows)."""
    body = await service.audit_csv(db, flt)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="audit.exported",
        actor_id=admin.id,
        actor_role=admin.role,
        target="audit",
    )
    await db.commit()
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="audit.csv"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
