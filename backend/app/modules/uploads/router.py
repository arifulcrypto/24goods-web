from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apitoken import require_scope
from app.core.deps import current_user, get_db, get_res, optional_user
from app.core.errors import NotFound, Unauthorized
from app.core.features import require_feature
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import Attachment, User
from app.modules.common.views import attachment_view
from app.modules.content.site import tenant_settings
from app.modules.uploads import service
from app.modules.uploads.storage import FileStore
from app.modules.uploads.thumbnail import small_image

router = APIRouter(tags=["uploads"])


def get_store(res: Resources = Depends(get_res)) -> FileStore:
    return FileStore(res.settings.upload_dir)


@router.post("/uploads", dependencies=[Depends(require_scope("uploads"))])
async def upload(
    file: UploadFile = File(...),
    kind: str = Form(...),
    duration_ms: int | None = Form(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    store: FileStore = Depends(get_store),
) -> dict:
    if kind == "voice":
        require_feature(await tenant_settings(db, user.tenant_id), "voice_messages")
    await enforce(res.redis, "upload:user", str(user.id), 60, 3600)
    att = await service.create_upload(
        db, store, user=user, file=file, kind=kind, duration_ms=duration_ms
    )
    return attachment_view(att)  # type: ignore[return-value]


@router.get("/files/{file_id}")
async def get_file(
    file_id: uuid.UUID,
    variant: Literal["original", "small"] = "original",
    res: Resources = Depends(get_res),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
    store: FileStore = Depends(get_store),
) -> FileResponse:
    att = await db.scalar(
        select(Attachment).where(
            Attachment.id == file_id, Attachment.tenant_id == db.info["tenant_id"]
        )
    )
    if att is None or att.deleted_at is not None:
        raise NotFound("File not found")
    if not att.is_public:
        if user is None:
            raise Unauthorized("Authentication required")
        if not await service.can_read(db, att, user):
            raise NotFound("File not found")
    path = store.path_for(att.storage_key)
    if not path.is_file():
        raise NotFound("File not found")
    media_type = att.mime
    if variant == "small" and att.kind == "image":
        await enforce(
            res.redis, "image:preview", str(user.id) if user else str(att.tenant_id), 60, 60
        )
        path = await small_image(store, att.storage_key)
        media_type = "image/webp"
    disposition = "inline" if att.kind == "image" else "attachment"
    if att.kind == "file":
        media_type = "text/plain; charset=utf-8"
        disposition = "attachment; filename*=UTF-8''" + quote(att.name or "message.txt")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        # Images render inline; anything else (voice) is an attachment: <audio> still
        # plays it, but direct navigation never renders it as a document.
        "Content-Disposition": disposition,
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "public, max-age=86400" if att.is_public else "private, no-store",
    }
    return FileResponse(path, media_type=media_type, headers=headers)
