"""Low-level message insertion shared by the API and the delivery service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message


async def insert_message(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    sender_id: uuid.UUID | None,
    sender_role: str,
    type: str,
    text: str | None = None,
    attachment_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    client_msg_id: str | None = None,
) -> tuple[Message, bool]:
    """Insert (idempotent on ``(sender_id, client_msg_id)``). Returns (message, created)."""
    stmt = (
        insert(Message)
        .values(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            sender_role=sender_role,
            type=type,
            text=text,
            attachment_id=attachment_id,
            payload=payload,
            client_msg_id=client_msg_id,
        )
        .on_conflict_do_nothing(constraint="uq_messages_sender_client_msg")
        .returning(Message.id)
    )
    new_id = await db.scalar(stmt)
    created = new_id is not None
    if new_id is None:
        new_id = await db.scalar(
            select(Message.id).where(
                Message.sender_id == sender_id, Message.client_msg_id == client_msg_id
            )
        )
    msg = await db.get(Message, new_id)
    assert msg is not None
    if created:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_id=msg.id, updated_at=func.now())
        )
    return msg, created


# English fallback text for every system event (the client translates by ``payload.event``).
SYSTEM_TEXT: dict[str, str] = {
    "paid": "Payment received.",
    "delivered": "Your order has been delivered.",
    "completed": "Order completed. Thank you!",
    "expired": "The order expired before payment.",
    "manual_review": "This order needs a manual review by our team.",
    "price_changed": "The order price was changed to {amount} {currency}.",
    "order_cancelled": "The order was cancelled.",
    "quote_accepted": "Quote accepted. An order was created.",
    "quote_withdrawn": "The quote was withdrawn.",
    "agent_joined": "{name} joined the conversation.",
    "agent_left": "{name} left the conversation.",
    "conversation_closed": "The conversation was closed.",
    "conversation_reopened": "The conversation was reopened.",
}


def system_text(event: str, data: dict[str, Any]) -> str:
    template = SYSTEM_TEXT.get(event, event.replace("_", " ").capitalize() + ".")
    try:
        return template.format(**{k: str(v) for k, v in data.items()})
    except (KeyError, IndexError, ValueError):
        return template


async def post_system(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    event: str,
    text: str | None = None,
    **data: Any,
) -> Message | None:
    if conversation_id is None:
        return None
    msg, _ = await insert_message(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        sender_id=None,
        sender_role="system",
        type="system",
        text=(text or system_text(event, data))[:2000],
        payload={"event": event, **data},
    )
    return msg


async def order_card_messages(
    db: AsyncSession, conversation_id: uuid.UUID | None, order_id: uuid.UUID
) -> list[Message]:
    """The order_card message(s) of an order, to re-publish when its status changes."""
    if conversation_id is None:
        return []
    rows = await db.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.type == "order_card",
            Message.payload["order_id"].astext == str(order_id),
        )
    )
    return list(rows)
