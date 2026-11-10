from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import client_ip, current_user, get_db, get_lang, get_res
from app.core.errors import NotFound, Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.security import verify_password
from app.models import User
from app.modules.auth import codes
from app.modules.common.views import user_view
from app.modules.content.site import tenant_settings
from app.modules.me import service
from app.modules.me.schemas import (
    CredentialsIn,
    DeleteIn,
    EmailChangeConfirmIn,
    EmailChangeIn,
    ProfileIn,
)
from app.modules.uploads.avatar import avatar_from_upload, avatar_mime
from app.modules.uploads.router import get_store
from app.modules.uploads.storage import FileStore

router = APIRouter(prefix="/me", tags=["me"])


@router.patch("")
async def patch_me(
    body: ProfileIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return user_view(await service.update_profile(db, user, body, client_ip(request)))


class AnnouncementAckIn(BaseModel):
    version: int = Field(ge=1, le=1_000_000)


@router.get("/announcement-ack")
async def get_announcement_ack(user: User = Depends(current_user)) -> dict:
    return {"version": int(user.announcement_ack or 0)}


@router.post("/announcement-ack")
async def post_announcement_ack(
    body: AnnouncementAckIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """The buyer confirmed the mandatory announcement ``version`` (follows them across devices).
    Only the tenant's current or an earlier version can be acknowledged; never decreases."""
    await enforce(res.redis, "me:announcement-ack", str(user.id), 30, 3600)
    ts = await tenant_settings(db, user.tenant_id)
    current = int((ts.get("announcement") or {}).get("version") or 0)
    if body.version > current:
        raise Validation("Unknown announcement version")
    row = await db.scalar(
        select(User).where(User.id == user.id, User.tenant_id == user.tenant_id).with_for_update()
    )
    if row is not None and body.version > int(row.announcement_ack or 0):
        row.announcement_ack = body.version
        await audit(
            db,
            tenant_id=user.tenant_id,
            action="announcement.acknowledged",
            actor_id=user.id,
            actor_role=user.role,
            target=f"user:{user.id}",
            ip=client_ip(request),
            detail={"version": body.version},
        )
        await db.commit()
    return {"version": int(row.announcement_ack if row else 0)}


@router.post("/credentials")
async def credentials(
    body: CredentialsIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "me:credentials", str(user.id), 10, 3600)
    token = res.sessions.token_from(request.cookies)
    out = await service.change_credentials(db, res, user, body, token, client_ip(request))
    return user_view(out)


@router.post("/email-change")
async def email_change(
    body: EmailChangeIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    lang: str = Depends(get_lang),
) -> dict:
    """Mail a code to the NEW address. Same ``{"sent": true}`` whether or not it is taken
    (a taken address simply receives nothing)."""
    await enforce(res.redis, "me:email_change", str(user.id), 10, 3600)
    await enforce(res.redis, "me:email_change:ip", client_ip(request) or "-", 20, 3600)
    if not user.is_instant:
        ok, _ = await verify_password(user.pw_hash, body.current_password or "")
        if not ok:
            raise service.InvalidPassword("Current password is incorrect")
    ts = await tenant_settings(db, user.tenant_id)
    await codes.request_email_change_code(db, res, user, ts, body.email, lang)
    return {"sent": True}


@router.post("/email-change/confirm")
async def email_change_confirm(
    body: EmailChangeConfirmIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    lang: str = Depends(get_lang),
) -> dict:
    out = await service.confirm_email_change(
        db,
        res,
        user,
        body.email,
        body.code,
        client_ip(request),
        current_token=res.sessions.token_from(request.cookies),
        lang=lang,
    )
    return user_view(out)


@router.post("/delete", status_code=204)
async def delete(
    body: DeleteIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    store: FileStore = Depends(get_store),
) -> Response:
    await enforce(res.redis, "me:delete", str(user.id), 5, 3600)
    await service.delete_account(db, res, user, body, client_ip(request), store)
    resp = Response(status_code=204)
    res.sessions.clear_cookie(resp)
    return resp


@router.put("/avatar")
async def put_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    store: FileStore = Depends(get_store),
) -> dict:
    await enforce(res.redis, "me:avatar", str(user.id), 20, 3600)
    processed = await avatar_from_upload(file)
    return user_view(await service.set_avatar(db, store, user, processed, client_ip(request)))


@router.delete("/avatar")
async def delete_avatar(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    store: FileStore = Depends(get_store),
) -> dict:
    await enforce(res.redis, "me:avatar", str(user.id), 20, 3600)
    return user_view(await service.clear_avatar(db, store, user, client_ip(request)))


avatar_router = APIRouter(tags=["me"])


@avatar_router.get("/avatars/{user_id}")
async def get_avatar(
    user_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    store: FileStore = Depends(get_store),
) -> FileResponse:
    key = await service.avatar_for_viewer(db, user_id, user)
    path = store.path_for(key) if key else None
    if path is None or not path.is_file():
        raise NotFound("Avatar not found")  # no existence oracle
    return FileResponse(
        path,
        media_type=avatar_mime(path),
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Content-Disposition": "inline",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Cache-Control": "private, no-cache",  # revalidate: removal/visibility apply at once
            "Vary": "Cookie",
        },
    )
