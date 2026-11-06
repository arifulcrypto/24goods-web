from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, InvalidState, NotFound, Validation
from app.core.i18n import pick
from app.core.money import fmt
from app.core.resources import Resources
from app.core.security import STAFF_ROLES
from app.models import Attachment, Conversation, Message, Order, Product, Quote, User
from app.modules.catalog.service import get_active_product
from app.modules.chat.messages import insert_message
from app.modules.common.views import iso, message_view, product_card_payload
from app.modules.realtime.publish import publish_conversation, publish_message
from app.modules.uploads.service import text_preview
from app.modules.uploads.storage import FileStore

MAX_PAGE = 100


async def users_by_id(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = await db.scalars(select(User).where(User.id.in_(ids)))
    return {u.id: u for u in rows}


async def message_views(db: AsyncSession, msgs: list[Message]) -> list[dict[str, Any]]:
    if not msgs:
        return []
    users = await users_by_id(db, {m.sender_id for m in msgs if m.sender_id})
    att_ids = {m.attachment_id for m in msgs if m.attachment_id}
    atts: dict[uuid.UUID, Attachment] = {}
    if att_ids:
        atts = {
            a.id: a for a in await db.scalars(select(Attachment).where(Attachment.id.in_(att_ids)))
        }
    quote_ids: set[uuid.UUID] = set()
    order_ids: set[uuid.UUID] = set()
    for m in msgs:
        p = m.payload or {}
        if m.type == "quote_card" and p.get("quote_id"):
            quote_ids.add(uuid.UUID(p["quote_id"]))
        if m.type == "order_card" and p.get("order_id"):
            order_ids.add(uuid.UUID(p["order_id"]))
    quotes = (
        {q.id: q for q in await db.scalars(select(Quote).where(Quote.id.in_(quote_ids)))}
        if quote_ids
        else {}
    )
    orders = (
        {o.id: o for o in await db.scalars(select(Order).where(Order.id.in_(order_ids)))}
        if order_ids
        else {}
    )
    out = []
    for m in msgs:
        overlay: dict[str, Any] | None = None
        p = m.payload or {}
        if m.type == "quote_card" and p.get("quote_id"):
            q = quotes.get(uuid.UUID(p["quote_id"]))
            if q is not None:
                overlay = {
                    "status": q.status,
                    "order_id": str(q.order_id) if q.order_id else None,
                    "expires_at": iso(q.expires_at),
                }
        elif m.type == "order_card" and p.get("order_id"):
            o = orders.get(uuid.UUID(p["order_id"]))
            if o is not None:
                overlay = {"status": o.status, "amount": fmt(o.amount_minor)}
        sender = users.get(m.sender_id) if m.sender_id else None
        out.append(
            message_view(
                m,
                sender_name=sender.display_name if sender else None,
                attachment=atts.get(m.attachment_id) if m.attachment_id else None,
                payload_overlay=overlay,
            )
        )
    return out


async def conversation_views(
    db: AsyncSession, convs: list[Conversation], *, viewer: str, lang: str
) -> list[dict[str, Any]]:
    if not convs:
        return []
    users = await users_by_id(
        db,
        {c.buyer_id for c in convs} | {c.assigned_agent_id for c in convs if c.assigned_agent_id},
    )
    pids = {c.product_id for c in convs if c.product_id}
    products = (
        {p.id: p for p in await db.scalars(select(Product).where(Product.id.in_(pids)))}
        if pids
        else {}
    )
    last_ids = [c.last_message_id for c in convs if c.last_message_id]
    last_msgs = (
        list(await db.scalars(select(Message).where(Message.id.in_(last_ids)))) if last_ids else []
    )
    last_views = {v["id"]: v for v in await message_views(db, last_msgs)}
    ids = [c.id for c in convs]
    if viewer == "buyer":
        cond = and_(Message.id > Conversation.buyer_last_read_id, Message.sender_role != "buyer")
    else:
        cond = and_(Message.id > Conversation.staff_last_read_id, Message.sender_role == "buyer")
    unread_rows = await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.conversation_id.in_(ids), cond)
        .group_by(Message.conversation_id)
    )
    unread = {cid: int(n) for cid, n in unread_rows.all()}
    out = []
    for c in convs:
        buyer = users.get(c.buyer_id)
        agent = users.get(c.assigned_agent_id) if c.assigned_agent_id else None
        product = products.get(c.product_id) if c.product_id else None
        out.append(
            {
                "id": str(c.id),
                "buyer": {
                    "id": str(c.buyer_id),
                    "display_name": buyer.display_name if buyer else "",
                },
                "product": product_card_payload(product, lang) if product else None,
                "status": c.status,
                "assigned_agent": (
                    {"id": str(agent.id), "display_name": agent.display_name} if agent else None
                ),
                "last_message": last_views.get(c.last_message_id) if c.last_message_id else None,
                "unread": unread.get(c.id, 0),
                "updated_at": iso(c.updated_at),
            }
        )
    return out


async def broadcast(
    res: Resources, db: AsyncSession, conv: Conversation, msgs: list[Message]
) -> None:
    """Publish message(s) + refreshed conversation summaries (call after commit)."""
    await db.refresh(conv)
    for view in await message_views(db, msgs):
        await publish_message(res.redis, conv.tenant_id, conv.buyer_id, view)
    buyer = await db.get(User, conv.buyer_id)
    lang = buyer.locale if buyer else "en"
    (bview,) = await conversation_views(db, [conv], viewer="buyer", lang=lang)
    (sview,) = await conversation_views(db, [conv], viewer="staff", lang=lang)
    await publish_conversation(res.redis, conv.tenant_id, conv.buyer_id, bview, sview)


async def buyer_conversation(db: AsyncSession, conv_id: uuid.UUID, user: User) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.buyer_id != user.id:
        raise NotFound("Conversation not found")  # no existence oracle for IDOR probes
    return conv


async def staff_conversation(db: AsyncSession, conv_id: uuid.UUID) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if conv is None:
        raise NotFound("Conversation not found")
    return conv


async def open_conversation_for(db: AsyncSession, buyer: User) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(Conversation.buyer_id == buyer.id, Conversation.status == "open")
    )


async def find_or_create(
    db: AsyncSession,
    buyer: User,
    product_id: uuid.UUID | None,
    reseller_id: uuid.UUID | None = None,
) -> tuple[Conversation, list[Message]]:
    if buyer.role in STAFF_ROLES:
        raise Validation("Staff accounts cannot open buyer conversations")
    product = await get_active_product(db, product_id) if product_id else None
    await db.execute(
        insert(Conversation)
        .values(
            tenant_id=buyer.tenant_id,
            buyer_id=buyer.id,
            status="open",
            reseller_id=reseller_id,  # storefront the buyer came from (CS context); new rows only
        )
        .on_conflict_do_nothing(
            # Literal predicate: a bound parameter breaks index inference under generic plans.
            index_elements=["tenant_id", "buyer_id"],
            index_where=text("status = 'open'"),
        )
    )
    conv = await open_conversation_for(db, buyer)
    assert conv is not None
    new_msgs: list[Message] = []
    # Compare with the last product card actually in the thread, not conv.product_id: staff
    # can post cards for other products (v4) without changing conv.product_id, and a buyer
    # coming back to the earlier product must get its card again.
    last_card = (
        await db.scalar(
            select(Message.payload)
            .where(Message.conversation_id == conv.id, Message.type == "product_card")
            .order_by(Message.id.desc())
            .limit(1)
        )
        if product is not None
        else None
    )
    if product is not None and (last_card or {}).get("product_id") != str(product.id):
        msg, _ = await insert_message(
            db,
            tenant_id=buyer.tenant_id,
            conversation_id=conv.id,
            sender_id=buyer.id,
            sender_role="buyer",
            type="product_card",
            payload=product_card_payload(product, buyer.locale),
        )
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conv.id)
            .values(product_id=product.id, buyer_last_read_id=msg.id)
        )
        new_msgs.append(msg)
    await db.commit()
    await db.refresh(conv)
    return conv, new_msgs


async def list_messages(
    db: AsyncSession,
    conv: Conversation,
    after_id: int | None,
    before_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, MAX_PAGE))
    stmt = select(Message).where(Message.conversation_id == conv.id)
    if before_id is not None:
        rows = list(
            await db.scalars(
                stmt.where(Message.id < before_id).order_by(Message.id.desc()).limit(limit)
            )
        )
        rows.reverse()
    elif after_id is not None:
        rows = list(
            await db.scalars(stmt.where(Message.id > after_id).order_by(Message.id).limit(limit))
        )
    else:
        rows = list(await db.scalars(stmt.order_by(Message.id.desc()).limit(limit)))
        rows.reverse()
    return await message_views(db, rows)


async def send_user_message(
    db: AsyncSession,
    conv: Conversation,
    sender: User,
    *,
    role: str,
    type: str,
    text: str | None,
    attachment_id: uuid.UUID | None,
    client_msg_id: str,
    store: FileStore | None = None,
    product_id: uuid.UUID | None = None,
) -> tuple[Message, bool]:
    payload: dict[str, Any] | None = None
    existing = await db.scalar(
        select(Message).where(
            Message.sender_id == sender.id, Message.client_msg_id == client_msg_id
        )
    )
    if existing is not None:
        if existing.conversation_id != conv.id:
            raise Conflict("client_msg_id already used")
        return existing, False
    if conv.status != "open":
        raise InvalidState("Conversation is closed")
    if type == "product_card":
        # staff push a product link; the payload comes from the product row, never the client
        if role != "agent":
            raise Validation("product_card messages are staff-only")
        if product_id is None:
            raise Validation("product_id is required")
        product = await get_active_product(db, product_id)  # active + same tenant (RLS)
        buyer = await db.get(User, conv.buyer_id)
        payload = product_card_payload(product, buyer.locale if buyer else "en")
        text = None
        attachment_id = None
    elif type == "text":
        text = (text or "").strip()
        if not text:
            raise Validation("text is required")
        if len(text) > 2000:
            raise Validation("text must be at most 2000 characters")
        attachment_id = None
    else:
        if attachment_id is None:
            raise Validation("attachment_id is required")
        att = await db.get(Attachment, attachment_id)
        if att is None or att.owner_id != sender.id or att.kind != type or att.purpose != "chat":
            raise NotFound("Attachment not found")
        text = None
        if type == "file":
            if store is None:
                raise Validation("file messages are not available")
            payload = {
                "name": att.name or "message.txt",
                "bytes": att.bytes,
                "preview": await text_preview(store, att),
            }
    msg, created = await insert_message(
        db,
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        sender_id=sender.id,
        sender_role=role,
        type=type,
        text=text,
        attachment_id=attachment_id,
        payload=payload,
        client_msg_id=client_msg_id,
    )
    if created:
        column = "buyer_last_read_id" if role == "buyer" else "staff_last_read_id"
        values: dict[str, Any] = {column: msg.id}
        if role == "agent" and conv.assigned_agent_id is None:
            values["assigned_agent_id"] = sender.id
        await db.execute(update(Conversation).where(Conversation.id == conv.id).values(**values))
    if not created and msg.conversation_id != conv.id:  # lost a race on client_msg_id
        await db.rollback()
        raise Conflict("client_msg_id already used")
    await db.commit()
    return msg, created


async def mark_read(db: AsyncSession, conv: Conversation, message_id: int, viewer: str) -> None:
    upto = min(message_id, conv.last_message_id or 0)
    column = (
        Conversation.buyer_last_read_id if viewer == "buyer" else Conversation.staff_last_read_id
    )
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conv.id)
        .values({column: func.greatest(column, upto)})
    )
    await db.commit()


def resolve_title(product: Product | None, lang: str) -> str:
    return pick(product.title_i18n, lang) if product else ""
