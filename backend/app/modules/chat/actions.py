"""Bounded sender recall and conversation pins; media access is revoked before deletion."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import client_ip, current_user, get_db, get_res
from app.core.errors import Forbidden, InvalidState, NotFound, Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import Attachment, Conversation, Message, Order, User
from app.modules.chat import service
from app.modules.chat.messages import post_system
from app.modules.chat.schemas import EditIn
from app.modules.uploads.storage import FileStore

router = APIRouter(tags=["chat"])


async def authorized(db: AsyncSession, mid: int, user: User) -> tuple[Message, Conversation]:
    msg = await db.scalar(
        select(Message).where(Message.id == mid, Message.tenant_id == user.tenant_id)
    )
    if msg is None:
        raise NotFound("Message not found")
    conv = await db.scalar(
        select(Conversation)
        .where(Conversation.id == msg.conversation_id, Conversation.tenant_id == user.tenant_id)
        .with_for_update()
    )
    if conv is None or (user.role not in ("admin", "agent") and conv.buyer_id != user.id):
        raise NotFound("Message not found")
    # Serialize mutations with send/recall/pin within this conversation.
    await db.refresh(msg, with_for_update=True)
    return msg, conv


async def _scrub_replies(db: AsyncSession, conv: Conversation, mid: int) -> list[Message]:
    """A recalled message must not live on in other messages' quoted preview."""
    rows = list(
        await db.scalars(
            select(Message).where(
                Message.tenant_id == conv.tenant_id,
                Message.conversation_id == conv.id,
                Message.payload["reply_to"]["id"].astext == str(mid),
            )
        )
    )
    for r in rows:
        rt = dict((r.payload or {}).get("reply_to") or {})
        rt["preview"] = ""
        r.payload = {**(r.payload or {}), "reply_to": rt}
    return rows


async def _refresh_reply_previews(
    db: AsyncSession, conv: Conversation, mid: int, text: str
) -> list[Message]:
    """An edited message must not live on in other messages' quoted preview either."""
    rows = list(
        await db.scalars(
            select(Message).where(
                Message.tenant_id == conv.tenant_id,
                Message.conversation_id == conv.id,
                Message.payload["reply_to"]["id"].astext == str(mid),
            )
        )
    )
    for r in rows:
        rt = dict((r.payload or {}).get("reply_to") or {})
        rt["preview"] = text[: service.REPLY_PREVIEW]
        r.payload = {**(r.payload or {}), "reply_to": rt}
    return rows


EDIT_WINDOW_S = 48 * 3600
EDIT_HISTORY_MAX = 5


@router.patch("/chat/messages/{mid}")
async def edit(
    mid: int,
    body: EditIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "chat:edit", str(user.id), 30, 60)
    msg, conv = await authorized(db, mid, user)
    p = msg.payload or {}
    if (
        msg.sender_id != user.id
        or msg.type != "text"
        or p.get("redacted")
        or p.get("recalled")
        or (msg.client_msg_id or "").startswith("order-input:")
    ):
        raise Forbidden("Only the sender can edit a text message")
    age = (datetime.now(timezone.utc) - msg.created_at).total_seconds()
    if not 0 <= age <= EDIT_WINDOW_S:
        raise InvalidState("The 48-hour edit window has expired")
    text = body.text.strip()
    if not text:
        raise Validation("text is required")
    before = msg.text or ""
    if text != before:
        now = datetime.now(timezone.utc).isoformat()
        history = [*(p.get("edit_history") or []), {"text": before, "edited_at": now}]
        msg.text = text
        msg.payload = {
            **p,
            "edited_at": now,
            "edit_history": history[-EDIT_HISTORY_MAX:],
        }
        await db.flush()
        replies = await _refresh_reply_previews(db, conv, mid, text)
        await audit(
            db,
            tenant_id=user.tenant_id,
            actor_id=user.id,
            actor_role=user.role,
            action="chat.message_edited",
            target=f"message:{mid}",
            before={"text": before},
            after={"text": text},
            ip=client_ip(request),
            detail={"chars_before": len(before), "chars_after": len(text)},
        )
        await db.commit()
        await service.broadcast(res, db, conv, [msg, *replies])
    return (await service.message_views(db, [msg]))[0]


@router.post("/chat/messages/{mid}/recall")
async def recall(
    mid: int,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "chat:recall", str(user.id), 30, 60)
    msg, conv = await authorized(db, mid, user)
    if msg.sender_id != user.id or msg.type not in ("text", "image", "voice", "file"):
        raise Forbidden("Only the sender can recall this message")
    if (msg.payload or {}).get("recalled"):
        return (await service.message_views(db, [msg]))[0]
    age = (datetime.now(timezone.utc) - msg.created_at).total_seconds()
    if not 0 <= age <= 180:
        raise InvalidState("The 3-minute recall window has expired")
    msg.text = None
    msg.payload = {"recalled": True, "redacted": True}
    await db.flush()
    scrubbed = await _scrub_replies(db, conv, mid)
    key = None
    if msg.attachment_id:
        att = await db.scalar(
            select(Attachment)
            .where(Attachment.id == msg.attachment_id, Attachment.tenant_id == user.tenant_id)
            .with_for_update()
        )
        used = await db.scalar(
            select(
                exists().where(
                    Message.tenant_id == user.tenant_id,
                    Message.attachment_id == msg.attachment_id,
                    Message.id != msg.id,
                    Message.payload["recalled"].as_boolean().is_not(True),
                )
            )
        )
        delivered = await db.scalar(
            select(
                exists().where(
                    Order.tenant_id == user.tenant_id,
                    Order.manual_delivery_attachment_id == msg.attachment_id,
                )
            )
        )
        if att and not att.is_public and att.purpose == "chat" and not used and not delivered:
            att.deleted_at = datetime.now(timezone.utc)
            key = att.storage_key
    await audit(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_role=user.role,
        action="chat.message_recalled",
        target=f"message:{mid}",
        ip=client_ip(request),
    )
    await db.commit()
    await service.broadcast(res, db, conv, [msg, *scrubbed])
    if key:
        # Tombstone is durable first. Purge jobs can retry an interrupted physical deletion.
        try:
            store = FileStore(res.settings.upload_dir)
            await store.delete(key)
            from app.modules.uploads.thumbnail import delete_thumbnail

            await delete_thumbnail(store, key)
        except OSError:
            pass
    return (await service.message_views(db, [msg]))[0]


class PinIn(BaseModel):
    pinned: bool


@router.post("/chat/messages/{mid}/pin")
async def pin(
    mid: int,
    body: PinIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "chat:pin", str(user.id), 20, 60)
    msg, conv = await authorized(db, mid, user)
    if msg.type not in ("text", "image", "voice", "file") or (msg.payload or {}).get("redacted"):
        raise InvalidState("This message cannot be pinned")
    if body.pinned:
        count = list(
            await db.scalars(
                select(Message.id)
                .where(
                    Message.tenant_id == user.tenant_id,
                    Message.conversation_id == conv.id,
                    Message.payload["pinned"].as_boolean().is_(True),
                )
                .limit(4)
            )
        )
        if len(count) >= 3 and msg.id not in count:
            raise InvalidState("A conversation supports up to 3 pins")
    was_pinned = bool((msg.payload or {}).get("pinned"))
    msg.payload = {**(msg.payload or {}), "pinned": body.pinned}
    extra: list[Message] = []
    if body.pinned != was_pinned:
        line = await post_system(
            db,
            tenant_id=user.tenant_id,
            conversation_id=conv.id,
            event="message_pinned" if body.pinned else "message_unpinned",
            name=user.display_name,
            message_id=msg.id,
        )
        if line is not None:
            extra.append(line)
    await audit(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_role=user.role,
        action="chat.message_pin_changed",
        target=f"message:{mid}",
        ip=client_ip(request),
        detail={"pinned": body.pinned},
    )
    await db.commit()
    await service.broadcast(res, db, conv, [msg, *extra])
    return (await service.message_views(db, [msg]))[0]
