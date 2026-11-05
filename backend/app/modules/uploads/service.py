from __future__ import annotations

import asyncio
import uuid

import anyio.to_thread
from fastapi import UploadFile
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UploadRejected
from app.core.security import STAFF_ROLES
from app.models import Attachment, Conversation, Message, Order, User
from app.modules.uploads.processing import (
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_VOICE_BYTES,
    Processed,
    process_image,
    process_text,
    process_voice,
    sanitize_filename,
)
from app.modules.uploads.storage import FileStore

# One decode at a time keeps peak RSS bounded (a 40 MP RGBA frame is ~160 MB).
_image_slot = asyncio.Semaphore(1)


async def read_limited(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise UploadRejected("File too large")
    return data


async def image_from_upload(file: UploadFile) -> Processed:
    data = await read_limited(file, MAX_IMAGE_BYTES)
    async with _image_slot:
        return await anyio.to_thread.run_sync(process_image, data)


async def store_attachment(
    db: AsyncSession,
    store: FileStore,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    kind: str,
    processed: Processed,
    purpose: str = "chat",
    is_public: bool = False,
    duration_ms: int | None = None,
    name: str | None = None,
) -> Attachment:
    key = await store.write(processed.data)
    att = Attachment(
        tenant_id=tenant_id,
        owner_id=owner_id,
        kind=kind,
        purpose=purpose,
        is_public=is_public,
        mime=processed.mime,
        bytes=len(processed.data),
        width=processed.width,
        height=processed.height,
        duration_ms=duration_ms,
        storage_key=key,
        name=name,
    )
    db.add(att)
    await db.flush()
    return att


async def create_upload(
    db: AsyncSession,
    store: FileStore,
    *,
    user: User,
    file: UploadFile,
    kind: str,
    duration_ms: int | None,
) -> Attachment:
    if kind == "image":
        processed = await image_from_upload(file)
        duration = None
    elif kind == "voice":
        data = await read_limited(file, MAX_VOICE_BYTES)
        processed = process_voice(data, duration_ms)
        duration = duration_ms
    elif kind == "file":
        processed = process_text(await read_limited(file, MAX_FILE_BYTES))
        duration = None
    else:
        raise UploadRejected("kind must be image, voice or file")
    att = await store_attachment(
        db,
        store,
        tenant_id=user.tenant_id,
        owner_id=user.id,
        kind=kind,
        processed=processed,
        duration_ms=duration,
        name=sanitize_filename(file.filename) if kind == "file" else None,
    )
    await db.commit()
    return att


PREVIEW_CHARS = 160


async def text_preview(store: FileStore, att: Attachment) -> str:
    def _read() -> str:
        with store.path_for(att.storage_key).open("rb") as fh:
            return fh.read(PREVIEW_CHARS * 4).decode("utf-8", errors="ignore")[:PREVIEW_CHARS]

    return await anyio.to_thread.run_sync(_read)


async def can_read(db: AsyncSession, att: Attachment, user: User) -> bool:
    if att.is_public or att.owner_id == user.id or user.role in STAFF_ROLES:
        return True
    in_conversation = await db.scalar(
        select(
            exists().where(
                Message.attachment_id == att.id,
                Message.conversation_id == Conversation.id,
                Conversation.buyer_id == user.id,
            )
        )
    )
    if in_conversation:
        return True
    in_order = await db.scalar(
        select(
            exists().where(Order.manual_delivery_attachment_id == att.id, Order.buyer_id == user.id)
        )
    )
    return bool(in_order)
