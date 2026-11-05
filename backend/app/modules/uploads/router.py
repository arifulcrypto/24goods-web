from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user, get_db, get_res, optional_user
from app.core.errors import NotFound, Unauthorized
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import Attachment, User
from app.modules.common.views import attachment_view
from app.modules.uploads import service
from app.modules.uploads.storage import FileStore

router = APIRouter(tags=["uploads"])


def get_store(res: Resources = Depends(get_res)) -> FileStore:
    return FileStore(res.settings.upload_dir)


@router.post("/uploads")
async def upload(
    file: UploadFile = File(...),
    kind: str = Form(...),
    duration_ms: int | None = Form(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    store: FileStore = Depends(get_store),
) -> dict:
    await enforce(res.redis, "upload:user", str(user.id), 60, 3600)
    att = await service.create_upload(
        db, store, user=user, file=file, kind=kind, duration_ms=duration_ms
    )
    return attachment_view(att)  # type: ignore[return-value]


@router.get("/files/{file_id}")
async def get_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
    store: FileStore = Depends(get_store),
) -> FileResponse:
    att = await db.get(Attachment, file_id)
    if att is None:
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
        "Cache-Control": "public, max-age=86400" if att.is_public else "private, max-age=300",
    }
    return FileResponse(path, media_type=media_type, headers=headers)
