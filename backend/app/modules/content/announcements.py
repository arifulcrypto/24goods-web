"""Announcements (review-v11 W3): ``articles`` rows with ``kind = 'notice'`` + per-user read state.

Buyers/staff read through ``/announcements``; admins manage through ``/admin/announcements``.
Settings live in ``tenants.settings['announcements']`` (``prompt_new``, ``show_badge``).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import (
    Page,
    client_ip,
    current_user,
    get_db,
    get_lang,
    get_page,
    get_res,
    optional_user,
    require_admin,
)
from app.core.errors import NotFound
from app.core.i18n import pick
from app.core.markdown import render_i18n
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.scoped import session_tenant
from app.models import AnnouncementRead, Article, Tenant, User
from app.modules.common.views import iso
from app.modules.content.schemas import _i18n

CATEGORIES = ("news", "operations", "inventory", "notice")
LOCALES = ("en", "zh-CN")

router = APIRouter(tags=["announcements"])
admin = APIRouter(prefix="/admin/announcements", tags=["admin"])


def _full(values: dict[str, str] | None) -> dict[str, str]:
    values = values or {}
    return {k: values.get(k, "") for k in LOCALES}


def config(ts: dict[str, Any] | None) -> dict[str, bool]:
    raw = (ts or {}).get("announcements")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "prompt_new": bool(raw.get("prompt_new", True)),
        "show_badge": bool(raw.get("show_badge", True)),
    }


def _published(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (
        Article.tenant_id == tenant_id,
        Article.kind == "notice",
        Article.status == "published",
    )


def _unread_clause(user: User):  # type: ignore[no-untyped-def]
    """Published after the account existed and not yet read by it."""
    read = select(AnnouncementRead.article_id).where(
        AnnouncementRead.tenant_id == user.tenant_id, AnnouncementRead.user_id == user.id
    )
    return (
        *_published(user.tenant_id),
        Article.published_at >= user.created_at,
        Article.id.not_in(read),
    )


async def unread_count(db: AsyncSession, user: User) -> int:
    return int(await db.scalar(select(func.count(Article.id)).where(*_unread_clause(user))) or 0)


async def newest_unread(db: AsyncSession, user: User) -> Article | None:
    return await db.scalar(
        select(Article)
        .where(*_unread_clause(user))
        .order_by(Article.published_at.desc(), Article.id)
        .limit(1)
    )


def _brief(a: Article, lang: str) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "title": pick(a.title_i18n, lang),
        "category": a.category,
        "pinned": a.pinned,
        "published_at": iso(a.published_at),
    }


async def site_block(
    db: AsyncSession, tenant_id: uuid.UUID, ts: dict[str, Any], lang: str, user: User | None
) -> dict[str, Any]:
    """The ``announcements`` part of ``GET /site`` (guests get the newest published one and
    compare it with their localStorage)."""
    cfg = config(ts)
    if user is not None and user.tenant_id == tenant_id:
        newest = await newest_unread(db, user)
        unread: int | None = await unread_count(db, user)
    else:
        newest = await db.scalar(
            select(Article)
            .where(*_published(tenant_id))
            .order_by(Article.published_at.desc(), Article.id)
            .limit(1)
        )
        unread = None
    return {
        **cfg,
        "unread": unread,
        "newest": _brief(newest, lang) if newest is not None else None,
    }


# ------------------------------------------------------------------ buyer / staff API
@router.get("/announcements")
async def list_announcements(
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    tenant_id = session_tenant(db)
    stmt = select(Article).where(*_published(tenant_id))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Article.pinned.desc(), Article.published_at.desc(), Article.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    read_ids: set[uuid.UUID] = set()
    if user is not None and rows:
        read_ids = set(
            await db.scalars(
                select(AnnouncementRead.article_id).where(
                    AnnouncementRead.tenant_id == user.tenant_id,
                    AnnouncementRead.user_id == user.id,
                    AnnouncementRead.article_id.in_([a.id for a in rows]),
                )
            )
        )
    items = [
        {**_brief(a, lang), "read": (a.id in read_ids) if user is not None else None} for a in rows
    ]
    out = page.wrap(items, int(total))
    out["unread"] = await unread_count(db, user) if user is not None else None
    return out


@router.post("/announcements/read-all", status_code=204)
async def read_all(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    await enforce(res.redis, "ann:read", str(user.id), 60, 3600)
    ids = list(await db.scalars(select(Article.id).where(*_unread_clause(user))))
    if ids:
        await db.execute(
            pg_insert(AnnouncementRead)
            .values(
                [{"tenant_id": user.tenant_id, "user_id": user.id, "article_id": i} for i in ids]
            )
            .on_conflict_do_nothing()
        )
        await db.commit()
    return Response(status_code=204)


async def _published_or_404(db: AsyncSession, tenant_id: uuid.UUID, ident: uuid.UUID) -> Article:
    a = await db.scalar(select(Article).where(Article.id == ident, *_published(tenant_id)))
    if a is None:
        raise NotFound("Announcement not found")
    return a


@router.get("/announcements/{ident}")
async def get_announcement(
    ident: uuid.UUID,
    lang: str = Depends(get_lang),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    a = await _published_or_404(db, session_tenant(db), ident)
    return {**_brief(a, lang), "body_html": pick(a.body_html_i18n, lang)}


@router.post("/announcements/{ident}/read", status_code=204)
async def mark_read(
    ident: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    await enforce(res.redis, "ann:read", str(user.id), 60, 3600)
    a = await _published_or_404(db, user.tenant_id, ident)
    await db.execute(
        pg_insert(AnnouncementRead)
        .values(tenant_id=user.tenant_id, user_id=user.id, article_id=a.id)
        .on_conflict_do_nothing()
    )
    await db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ admin API
class AnnouncementIn(BaseModel):
    title_i18n: dict[str, str]
    body_i18n: dict[str, str] = Field(default_factory=dict)
    category: Literal["news", "operations", "inventory", "notice"] = "news"
    status: Literal["draft", "published"] = "draft"
    pinned: bool = False

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=200, required=True)  # type: ignore[return-value]

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=20_000)  # type: ignore[return-value]


class AnnouncementPatch(BaseModel):
    title_i18n: dict[str, str] | None = None
    body_i18n: dict[str, str] | None = None
    category: Literal["news", "operations", "inventory", "notice"] | None = None
    status: Literal["draft", "published"] | None = None
    pinned: bool | None = None

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=200, required=True)

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=20_000)


class AnnouncementSettingsIn(BaseModel):
    prompt_new: bool | None = None
    show_badge: bool | None = None


def _admin_view(a: Article, reads: int) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "title_i18n": _full(a.title_i18n),
        "body_i18n": _full(a.body_i18n),
        "category": a.category,
        "status": a.status,
        "pinned": a.pinned,
        "published_at": iso(a.published_at),
        "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at),
        "read_count": reads,
    }


def _snap(a: Article) -> dict[str, Any]:
    return {"category": a.category, "status": a.status, "pinned": a.pinned}


async def _reads(
    db: AsyncSession, tenant_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(AnnouncementRead.article_id, func.count())
        .where(AnnouncementRead.tenant_id == tenant_id, AnnouncementRead.article_id.in_(ids))
        .group_by(AnnouncementRead.article_id)
    )
    return {k: int(n) for k, n in rows.all()}


async def _admin_get(db: AsyncSession, tenant_id: uuid.UUID, ident: uuid.UUID) -> Article:
    a = await db.scalar(
        select(Article)
        .where(Article.id == ident, Article.tenant_id == tenant_id, Article.kind == "notice")
        .with_for_update()
    )
    if a is None:
        raise NotFound("Announcement not found")
    return a


async def _tenant_settings(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    return dict(await db.scalar(select(Tenant.settings).where(Tenant.id == tenant_id)) or {})


@admin.get("/settings")
async def get_settings(
    staff: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    return config(await _tenant_settings(db, staff.tenant_id))


@admin.put("/settings")
async def put_settings(
    body: AnnouncementSettingsIn,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == staff.tenant_id).with_for_update())
    if tenant is None:
        raise NotFound("Tenant not found")
    cur = dict(tenant.settings or {})
    before = config(cur)
    after = {**before, **{k: v for k, v in body.model_dump().items() if v is not None}}
    tenant.settings = {**cur, "announcements": after}
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="announcement.settings",
        actor_id=staff.id,
        actor_role=staff.role,
        target="tenant:announcements",
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await db.commit()
    return after


@admin.get("")
async def admin_list(
    status: Literal["draft", "published"] | None = None,
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Article).where(Article.tenant_id == staff.tenant_id, Article.kind == "notice")
    if status:
        stmt = stmt.where(Article.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Article.pinned.desc(), Article.created_at.desc(), Article.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    reads = await _reads(db, staff.tenant_id, [a.id for a in rows])
    return page.wrap([_admin_view(a, reads.get(a.id, 0)) for a in rows], int(total))


@admin.post("", status_code=201)
async def admin_create(
    body: AnnouncementIn,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    body_i18n = _full(body.body_i18n)
    a = Article(
        tenant_id=staff.tenant_id,
        slug=f"n-{secrets.token_hex(6)}",
        kind="notice",
        category=body.category,
        title_i18n=_full(body.title_i18n),
        body_i18n=body_i18n,
        body_html_i18n=render_i18n(body_i18n),
        status=body.status,
        pinned=body.pinned,
        published_at=datetime.now(timezone.utc) if body.status == "published" else None,
        created_by=staff.id,
    )
    db.add(a)
    await db.flush()
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="announcement.published" if a.status == "published" else "announcement.created",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"announcement:{a.id}",
        after=_snap(a),
        ip=client_ip(request),
    )
    await db.commit()
    await db.refresh(a)
    return _admin_view(a, 0)


@admin.patch("/{ident}")
async def admin_patch(
    ident: uuid.UUID,
    body: AnnouncementPatch,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    a = await _admin_get(db, staff.tenant_id, ident)
    before = _snap(a)
    data = body.model_dump(exclude_unset=True)
    if data.get("title_i18n") is not None:
        a.title_i18n = _full(data["title_i18n"])
    if data.get("body_i18n") is not None:
        a.body_i18n = _full(data["body_i18n"])
        a.body_html_i18n = render_i18n(a.body_i18n)
    if data.get("category") is not None:
        a.category = data["category"]
    if data.get("pinned") is not None:
        a.pinned = data["pinned"]
    publishing = data.get("status") == "published" and a.status != "published"
    if data.get("status") is not None:
        if publishing and a.published_at is None:
            a.published_at = datetime.now(timezone.utc)
        a.status = data["status"]
    a.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="announcement.published" if publishing else "announcement.updated",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"announcement:{a.id}",
        before=before,
        after=_snap(a),
        ip=client_ip(request),
        detail={"fields": sorted(data)},
    )
    await db.commit()
    await db.refresh(a)
    reads = await _reads(db, staff.tenant_id, [a.id])
    return _admin_view(a, reads.get(a.id, 0))


@admin.delete("/{ident}", status_code=204)
async def admin_delete(
    ident: uuid.UUID,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    a = await _admin_get(db, staff.tenant_id, ident)
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="announcement.deleted",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"announcement:{a.id}",
        before=_snap(a),
        ip=client_ip(request),
    )
    await db.delete(a)
    await db.commit()
    return Response(status_code=204)
