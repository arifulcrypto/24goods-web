from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, current_user, get_db, get_res
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.common.views import user_view
from app.modules.me import service
from app.modules.me.schemas import CredentialsIn, DeleteIn, ProfileIn

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
) -> Response:
    await enforce(res.redis, "me:delete", str(user.id), 5, 3600)
    await service.delete_account(db, res, user, body, client_ip(request))
    resp = Response(status_code=204)
    res.sessions.clear_cookie(resp)
    return resp
