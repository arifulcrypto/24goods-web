"""Admin endpoints added in storefront v3: site settings, articles, card management."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page, client_ip, get_db, get_page, get_res, require_admin
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.admin.schemas import CardReplaceIn, CardsFindIn, CardsVoidIn
from app.modules.content import service as content
from app.modules.content.schemas import ArticleIn, ArticlePatch, SiteIn
from app.modules.inventory import service as inventory

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------- site
@router.get("/site")
async def get_site(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    return await content.admin_site(db, admin.tenant_id)


@router.put("/site")
async def put_site(
    body: SiteIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await content.update_site(db, admin, body, client_ip(request))


# ---------------------------------------------------------------- articles
@router.get("/articles")
async def list_articles(
    status: Literal["draft", "published"] | None = None,
    kind: Literal["article", "page"] | None = None,
    category: str | None = Query(None, max_length=32),
    q: str | None = Query(None, max_length=100),
    page: Page = Depends(get_page),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await content.admin_list(db, page, status, kind, category, q)


@router.post("/articles")
async def create_article(
    body: ArticleIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return content.article_admin_view(
        await content.admin_create(db, admin, body, client_ip(request))
    )


@router.get("/articles/{article_id}")
async def get_article(
    article_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    return content.article_admin_view(await content.admin_get(db, article_id))


@router.patch("/articles/{article_id}")
async def patch_article(
    article_id: uuid.UUID,
    body: ArticlePatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    a = await content.admin_patch(db, admin, article_id, body, client_ip(request))
    return content.article_admin_view(a)


@router.delete("/articles/{article_id}", status_code=204)
async def delete_article(
    article_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await content.admin_delete(db, admin, article_id, client_ip(request))
    return Response(status_code=204)


# ---------------------------------------------------------------- cards
@router.get("/products/{product_id}/batches")
async def list_batches(
    product_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    return {"items": await inventory.batches(db, product_id)}


@router.post("/cards/find")
async def find_cards(
    body: CardsFindIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    # 30/h per admin: a "does this exact code exist here?" oracle must stay slow
    await enforce(res.redis, "cards:find", str(admin.id), 30, 3600)
    out = await inventory.find_cards(db, res.sealer, admin.tenant_id, body.lines, body.by)
    await audit(  # counts only: the pasted values are never logged
        db,
        tenant_id=admin.tenant_id,
        action="cards.find",
        actor_id=admin.id,
        actor_role=admin.role,
        ip=client_ip(request),
        detail={
            "lines": len([ln for ln in body.lines if ln and ln.strip()]),
            "found": len(out["items"]),
            "missing": out["missing"],
            "mode": body.by,
        },
    )
    await db.commit()
    return out


@router.post("/cards/void")
async def void_cards(
    body: CardsVoidIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "cards:void", str(admin.id), 60, 3600)
    n = await inventory.void_cards(
        db,
        tenant_id=admin.tenant_id,
        ids=body.ids,
        batch_id=body.batch_id,
        reason=body.reason.strip(),
        actor_id=admin.id,
        actor_role=admin.role,
        ip=client_ip(request),
    )
    return {"voided": n}


@router.post("/cards/{card_id}/replace")
async def replace_card(
    card_id: uuid.UUID,
    body: CardReplaceIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "cards:replace", str(admin.id), 120, 3600)
    return await inventory.replace_card(
        db,
        res.sealer,
        tenant_id=admin.tenant_id,
        card_id=card_id,
        code=body.code,
        actor_id=admin.id,
        actor_role=admin.role,
        ip=client_ip(request),
    )
