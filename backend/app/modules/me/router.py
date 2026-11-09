from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, current_user, get_db, get_res
from app.core.errors import NotFound
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.common.views import user_view
from app.modules.me import service
from app.modules.me.schemas import CredentialsIn, DeleteIn, ProfileIn
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
            "Cache-Control": "private, max-age=3600",
        },
    )
