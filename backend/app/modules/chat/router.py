from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, current_user, get_db, get_lang, get_res
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.storefront import Storefront, get_storefront
from app.models import Conversation, User
from app.modules.chat import quotes as quote_svc
from app.modules.chat import service
from app.modules.chat.schemas import ConversationIn, MessageIn, ReadIn
from app.modules.orders.router import ORDER_ACTIONS
from app.modules.orders.service import broadcast_order, one_view
from app.modules.uploads.storage import FileStore

router = APIRouter(tags=["chat"])


@router.post("/conversations")
async def create_conversation(
    body: ConversationIn,
    user: User = Depends(current_user),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    conv, msgs = await service.find_or_create(
        db, user, body.product_id, sf.id if sf is not None else None
    )
    if msgs:
        await service.broadcast(res, db, conv, msgs)
    (view,) = await service.conversation_views(db, [conv], viewer="buyer", lang=lang)
    return view


@router.get("/conversations")
async def list_conversations(
    user: User = Depends(current_user),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        await db.scalars(
            select(Conversation)
            .where(Conversation.buyer_id == user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(100)
        )
    )
    return await service.conversation_views(db, rows, viewer="buyer", lang=lang)


@router.get("/conversations/{conv_id}/messages")
async def list_messages(
    conv_id: uuid.UUID,
    after_id: int | None = Query(None, ge=0),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    conv = await service.buyer_conversation(db, conv_id, user)
    return await service.list_messages(db, conv, after_id, before_id, limit)


@router.post("/conversations/{conv_id}/messages")
async def send_message(
    conv_id: uuid.UUID,
    body: MessageIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    conv = await service.buyer_conversation(db, conv_id, user)
    await enforce(res.redis, "msg:user", str(user.id), 30, 60)
    msg, created = await service.send_user_message(
        db,
        conv,
        user,
        role="buyer",
        type=body.type,
        text=body.text,
        attachment_id=body.attachment_id,
        client_msg_id=body.client_msg_id,
        store=FileStore(res.settings.upload_dir),
    )
    if created:
        await service.broadcast(res, db, conv, [msg])
    (view,) = await service.message_views(db, [msg])
    return view


@router.post("/conversations/{conv_id}/read", status_code=204)
async def mark_read(
    conv_id: uuid.UUID,
    body: ReadIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conv = await service.buyer_conversation(db, conv_id, user)
    await service.mark_read(db, conv, body.message_id, "buyer")
    return Response(status_code=204)


@router.post("/quotes/{quote_id}/accept")
async def accept_quote(
    quote_id: uuid.UUID,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "order:create", str(user.id), *ORDER_ACTIONS)
    order, quote, msgs = await quote_svc.accept_quote(
        db, res.settings, quote_id, user, client_ip(request)
    )
    qmsg = await quote_svc.quote_message(db, quote)  # same id, updated status/order_id
    code = getattr(order, "access_code_plain", None)
    await broadcast_order(res, db, order, [qmsg, *msgs])
    view = await one_view(db, order, user.id, lang)
    view["access_code"] = code
    return view
