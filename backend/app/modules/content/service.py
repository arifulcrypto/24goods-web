"""Site settings + articles CMS (public and admin)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import Text, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email_templates, globallimit, ipblock, secretbox
from app.core.audit import audit
from app.core.config import Settings
from app.core.deps import Page
from app.core.errors import Conflict, NotFound, Validation
from app.core.i18n import pick
from app.core.markdown import render_i18n
from app.core.settings_v5 import merge, section
from app.models import Article, Tenant, User
from app.modules.common.sitedefaults import (
    DEFAULT_AFTER_SALES_HOURS,
    DEFAULT_AUTO_CONFIRM_HOURS,
    after_sales_default_hours,
    auto_confirm_hours,
)
from app.modules.common.views import iso
from app.modules.content.schemas import ArticleIn, ArticlePatch, SiteIn
from app.modules.content.site import tenant_settings
from app.modules.payments.channels import admin_channels

LOCALES = ("en", "zh-CN")
log = logging.getLogger("app.content")
V5_SECTIONS = (
    "general",
    "legal",
    "features",
    "security",
    "defaults",
    "email",
    "notifications",
    "delivery",
    "import",
    "resellers",
    "audit",
)


def _full(values: dict[str, str] | None) -> dict[str, str]:
    values = values or {}
    return {k: values.get(k, "") for k in LOCALES}


# ------------------------------------------------------------------ site settings
def _announcement_admin(raw: dict[str, Any] | None) -> dict[str, Any]:
    a = raw or {}
    return {
        "version": int(a.get("version") or 0),
        "enabled": bool(a.get("enabled", False)),
        "title_i18n": _full(a.get("title_i18n")),
        "body_i18n": _full(a.get("body_i18n")),
        "body_html_i18n": _full(a.get("body_html_i18n")),
    }


def _quick_questions(raw: Any) -> dict[str, list[str]]:
    r = raw if isinstance(raw, dict) else {}
    return {k: [str(q) for q in (r.get(k) or [])][:6] for k in LOCALES}


def _support_cfg(ts: dict[str, Any]) -> dict[str, Any]:
    sup = ts.get("support") if isinstance(ts.get("support"), dict) else {}
    return {
        "welcome_i18n": _full(sup.get("welcome_i18n")),
        "quick_questions_i18n": _quick_questions(sup.get("quick_questions_i18n")),
    }


def _support_hours(raw: Any) -> dict[str, str]:
    return _full(raw if isinstance(raw, dict) else {"en": str(raw or ""), "zh-CN": str(raw or "")})


async def public_site(
    db: AsyncSession, tenant_id: uuid.UUID, lang: str, brand: str
) -> dict[str, Any]:
    ts = await tenant_settings(db, tenant_id)
    a = _announcement_admin(ts.get("announcement"))
    announcement = None
    if a["enabled"] and a["version"] > 0 and (a["title_i18n"]["en"] or a["body_i18n"]["en"]):
        announcement = {
            "version": a["version"],
            "title": pick(a["title_i18n"], lang),
            "body_html": pick(a["body_html_i18n"], lang),
            "enabled": True,
        }
    sup = _support_cfg(ts)
    pages = (
        await db.execute(
            select(Article.slug, Article.title_i18n)
            .where(Article.kind == "page", Article.status == "published")
            .order_by(Article.slug)
        )
    ).all()
    v5 = await _public_v5(db, tenant_id, ts, lang, pages)
    return {
        "brand": brand,
        **v5,
        "announcement": announcement,
        "after_sales_default_hours": after_sales_default_hours(ts),
        "support_hours": pick(_support_hours(ts.get("support_hours")), lang),
        "support": {
            "welcome": pick(sup["welcome_i18n"], lang),
            "quick_questions": sup["quick_questions_i18n"].get(lang)
            or sup["quick_questions_i18n"]["en"],
        },
        "pages": [{"slug": s, "title": pick(t, lang)} for s, t in pages],
    }


async def _public_v5(
    db: AsyncSession, tenant_id: uuid.UUID, ts: dict[str, Any], lang: str, pages: list
) -> dict[str, Any]:
    """v5 public blocks of ``GET /site``. Never secrets: only whether one is configured."""
    sec = section(ts, "security")
    gen = section(ts, "general")
    legal = section(ts, "legal")
    delivery = section(ts, "delivery")
    configured = await secretbox.status(db, tenant_id)
    ts_ready = bool(
        sec["turnstile_enabled"]
        and sec["turnstile_site_key"]
        and configured["turnstile_secret"]["configured"]
    )
    by_slug = {slug: title for slug, title in pages}
    return {
        "features": section(ts, "features"),
        "general": {
            "site_name": pick(gen["site_name_i18n"], lang) if gen["site_name_i18n"] else "",
            "subtitle": pick(gen["subtitle_i18n"], lang) if gen["subtitle_i18n"] else "",
            "site_name_i18n": gen["site_name_i18n"],
            "subtitle_i18n": gen["subtitle_i18n"],
            "logo_url": gen["logo_url"],
            "contact": pick(gen["contact_i18n"], lang) if gen["contact_i18n"] else "",
            "footer": pick(gen["footer_i18n"], lang) if gen["footer_i18n"] else "",
        },
        "legal": {
            "enabled": legal["enabled"],
            "mode": legal["mode"],
            "updated_at": legal["updated_at"],
            "pages": [
                {"slug": sl, "title": pick(by_slug[sl], lang)}
                for sl in legal["page_slugs"]
                if sl in by_slug
            ],
        },
        "turnstile": {
            "enabled": ts_ready,
            "site_key": sec["turnstile_site_key"] if ts_ready else "",
            "actions": sec["turnstile_actions"] if ts_ready else [],
        },
        "google_login": bool(
            section(ts, "features")["google_login"]
            and sec["google_client_id"]
            and configured["google_client_secret"]["configured"]
        ),
        "delivery": {
            "manual_eta_hours": delivery["manual_eta_hours"],
            "manual_note": pick(delivery["manual_note_i18n"], lang),
        },
        "email_verify_on_register": bool(sec["email_verify_on_register"]),
        # storefront: added by the reseller work (review-v5 §7)
    }


async def admin_site(
    db: AsyncSession, tenant_id: uuid.UUID, settings: Settings | None = None
) -> dict[str, Any]:
    ts = await tenant_settings(db, tenant_id)
    v5: dict[str, Any] = {name: section(ts, name) for name in V5_SECTIONS}
    v5["email_templates"] = email_templates.normalize(ts)
    zpay_pid = settings.zpay_pid if settings is not None else ""
    return {
        **{k: v for k, v in v5.items() if k != "defaults"},
        "secrets": await secretbox.status(db, tenant_id),
        "zpay": {"configured": bool(zpay_pid), "pid_last4": zpay_pid[-4:] if zpay_pid else None},
        "announcement": _announcement_admin(ts.get("announcement")),
        "after_sales_default_hours": after_sales_default_hours(ts),
        "auto_confirm_hours": {
            "manual": auto_confirm_hours(ts, "manual"),
            "auto_card": auto_confirm_hours(ts, "auto_card"),
        },
        "support_hours": _support_hours(ts.get("support_hours")),
        "payments": {"channels": admin_channels(ts)},
        "support": _support_cfg(ts),
        "rate_limit": globallimit.normalize(ts.get("rate_limit")),
        # v5 "user defaults" section + the pre-v5 factory defaults (kept for old clients)
        "defaults": {
            **v5["defaults"],
            "after_sales_default_hours": DEFAULT_AFTER_SALES_HOURS,
            "auto_confirm_hours": dict(DEFAULT_AUTO_CONFIRM_HOURS),
            "rate_limit": dict(globallimit.DEFAULTS),
        },
    }


async def update_site(
    db: AsyncSession,
    admin: User,
    body: SiteIn,
    ip: str | None,
    redis: Redis | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == admin.tenant_id).with_for_update())
    if tenant is None:
        raise NotFound("Tenant not found")
    cur = dict(tenant.settings or {})
    before = await admin_site(db, admin.tenant_id, settings)
    new = dict(cur)
    if body.announcement is not None:
        old = _announcement_admin(cur.get("announcement"))
        inc = body.announcement
        title = _full(inc.title_i18n) if inc.title_i18n is not None else old["title_i18n"]
        text = _full(inc.body_i18n) if inc.body_i18n is not None else old["body_i18n"]
        enabled = inc.enabled if inc.enabled is not None else old["enabled"]
        changed = (
            title != old["title_i18n"]
            or text != old["body_i18n"]
            or (enabled and not old["enabled"])
        )
        new["announcement"] = {
            "version": old["version"] + 1 if changed else old["version"],
            "enabled": enabled,
            "title_i18n": title,
            "body_i18n": text,
            "body_html_i18n": render_i18n(text)
            if text != old["body_i18n"]
            else old["body_html_i18n"],
        }
    if body.after_sales_default_hours is not None:
        new["after_sales_default_hours"] = body.after_sales_default_hours
    if body.auto_confirm_hours is not None:
        new["auto_confirm_hours"] = {
            "manual": body.auto_confirm_hours.manual,
            "auto_card": body.auto_confirm_hours.auto_card,
        }
    if body.support_hours is not None:
        new["support_hours"] = _full(body.support_hours)
    if body.payments is not None:
        pay = dict(new.get("payments") or {})
        chans = {k: dict(v) for k, v in (pay.get("channels") or {}).items()}
        for cid, inc in body.payments.channels.items():
            cur_c = admin_channels(cur)[cid]
            chans[cid] = {
                "enabled": inc.enabled if inc.enabled is not None else cur_c["enabled"],
                "surcharge_permille": inc.surcharge_permille
                if inc.surcharge_permille is not None
                else cur_c["surcharge_permille"],
            }
        pay["channels"] = chans
        new["payments"] = pay
    if body.support is not None:
        old_s = _support_cfg(cur)
        inc_s = body.support
        new["support"] = {
            "welcome_i18n": _full(inc_s.welcome_i18n)
            if inc_s.welcome_i18n is not None
            else old_s["welcome_i18n"],
            "quick_questions_i18n": inc_s.quick_questions_i18n
            if inc_s.quick_questions_i18n is not None
            else old_s["quick_questions_i18n"],
        }
    if body.rate_limit is not None:
        old_r = globallimit.normalize(cur.get("rate_limit"))
        inc_r = body.rate_limit.model_dump(exclude_none=True)
        new["rate_limit"] = globallimit.normalize({**old_r, **inc_r})
    v5_changed: dict[str, dict[str, Any]] = {}
    for name in V5_SECTIONS:
        inc = getattr(body, "import_" if name == "import" else name)
        if inc is None:
            continue
        old_sec = section(cur, name)
        new_sec = merge(new, name, inc.model_dump(exclude_unset=True, exclude_none=True))
        if name == "legal" and any(
            new_sec[k] != old_sec[k] for k in ("enabled", "mode", "page_slugs")
        ):
            new_sec["updated_at"] = datetime.now(timezone.utc).isoformat()
        new[name] = new_sec
        if new_sec != old_sec:
            v5_changed[name] = {"before": old_sec, "after": new_sec}
    if body.email_templates is not None:
        old_t = email_templates.normalize(cur)
        new["email_templates"] = email_templates.stored_after_patch(
            new, body.email_templates.model_dump(exclude_unset=True, exclude_none=True)
        )
        if new["email_templates"] != old_t:
            v5_changed["email_templates"] = {"before": old_t, "after": new["email_templates"]}
    tenant.settings = new
    await db.flush()
    after = await admin_site(db, admin.tenant_id, settings)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="site.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"tenant:{admin.tenant_id}",
        before={
            "announcement_version": before["announcement"]["version"],
            "channels": before["payments"]["channels"],
            "rate_limit": before["rate_limit"],
        },
        after={
            "announcement_version": after["announcement"]["version"],
            "channels": after["payments"]["channels"],
            "rate_limit": after["rate_limit"],
        },
        ip=ip,
        detail={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    if v5_changed:  # section names + non-secret values (secrets never live in settings)
        await audit(
            db,
            tenant_id=admin.tenant_id,
            action="settings.updated",
            actor_id=admin.id,
            actor_role=admin.role,
            target=f"tenant:{admin.tenant_id}",
            before={k: v["before"] for k, v in v5_changed.items()},
            after={k: v["after"] for k, v in v5_changed.items()},
            ip=ip,
            detail={"sections": sorted(v5_changed)},
        )
    await db.commit()
    if (body.rate_limit is not None or body.security is not None) and redis is not None:
        try:  # mirror for the middleware (fail open: a Redis error never fails the save)
            await globallimit.mirror(
                redis,
                admin.tenant_id,
                after["rate_limit"],
                section(new, "security")["staff_ip_allowlist"],
            )
        except Exception:
            log.error("rate limit config mirror to Redis failed")
    if body.security is not None and redis is not None:
        try:  # the IP-block middleware rebuilds its copy (incl. the allowlist) from the DB
            await redis.delete(ipblock.cfg_key(admin.tenant_id))
        except Exception:
            log.error("ip block config invalidation failed")
    return after


# ------------------------------------------------------------------ articles
def article_admin_view(a: Article) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "slug": a.slug,
        "kind": a.kind,
        "category": a.category,
        "title_i18n": _full(a.title_i18n),
        "body_i18n": _full(a.body_i18n),
        "body_html_i18n": _full(a.body_html_i18n),
        "status": a.status,
        "pinned": a.pinned,
        "views": a.views,
        "published_at": iso(a.published_at),
        "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at),
    }


def _list_item(a: Article, lang: str) -> dict[str, Any]:
    return {
        "slug": a.slug,
        "title": pick(a.title_i18n, lang),
        "category": a.category,
        "published_at": iso(a.published_at),
        "updated_at": iso(a.updated_at),
        "views": a.views,
        "pinned": a.pinned,
    }


async def list_public(db: AsyncSession, lang: str, page: Page, category: str | None) -> dict:
    stmt = select(Article).where(Article.kind == "article", Article.status == "published")
    if category:
        stmt = stmt.where(Article.category == category)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(Article.pinned.desc(), Article.published_at.desc(), Article.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    return page.wrap([_list_item(a, lang) for a in rows], int(total))


async def get_public(
    db: AsyncSession, redis: Redis, lang: str, slug: str, viewer_key: str
) -> dict[str, Any]:
    a = await db.scalar(select(Article).where(Article.slug == slug, Article.status == "published"))
    if a is None:
        raise NotFound("Article not found")
    # Count a view once per viewer per 10 minutes (no write amplification from reloads/bots).
    if await redis.set(f"artview:{a.id}:{viewer_key}", b"1", nx=True, ex=600):
        views = a.views + 1
        await db.execute(
            update(Article)
            .where(Article.id == a.id)
            .values(views=Article.views + 1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        a.views = views
    return {
        "slug": a.slug,
        "title": pick(a.title_i18n, lang),
        "category": a.category,
        "kind": a.kind,
        "body_html": pick(a.body_html_i18n, lang),
        "published_at": iso(a.published_at),
        "updated_at": iso(a.updated_at),
        "views": a.views,
    }


async def admin_list(
    db: AsyncSession,
    page: Page,
    status: str | None,
    kind: str | None,
    category: str | None,
    q: str | None = None,
) -> dict:
    stmt = select(Article)
    if q and q.strip():
        like = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            or_(
                Article.slug.ilike(f"%{like}%", escape="\\"),
                cast(Article.title_i18n, Text).ilike(f"%{like}%", escape="\\"),
            )
        )
    if status:
        stmt = stmt.where(Article.status == status)
    if kind:
        stmt = stmt.where(Article.kind == kind)
    if category:
        stmt = stmt.where(Article.category == category)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(Article.updated_at.desc(), Article.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    return page.wrap([article_admin_view(a) for a in rows], int(total))


async def admin_get(db: AsyncSession, article_id: uuid.UUID, *, lock: bool = False) -> Article:
    stmt = select(Article).where(Article.id == article_id)
    if lock:
        stmt = stmt.with_for_update()
    a = await db.scalar(stmt)
    if a is None:
        raise NotFound("Article not found")
    return a


async def admin_create(db: AsyncSession, admin: User, body: ArticleIn, ip: str | None) -> Article:
    body_i18n = _full(body.body_i18n)
    a = Article(
        tenant_id=admin.tenant_id,
        slug=body.slug,
        kind=body.kind,
        category=body.category,
        title_i18n=_full(body.title_i18n),
        body_i18n=body_i18n,
        body_html_i18n=render_i18n(body_i18n),
        status=body.status,
        pinned=body.pinned,
        published_at=datetime.now(timezone.utc) if body.status == "published" else None,
        created_by=admin.id,
    )
    db.add(a)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("An article with this slug already exists") from None
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="article.created",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"article:{a.id}",
        after={"slug": a.slug, "status": a.status, "kind": a.kind},
        ip=ip,
    )
    await db.commit()
    await db.refresh(a)
    return a


async def admin_patch(
    db: AsyncSession, admin: User, article_id: uuid.UUID, body: ArticlePatch, ip: str | None
) -> Article:
    a = await admin_get(db, article_id, lock=True)
    before = {"slug": a.slug, "status": a.status, "pinned": a.pinned}
    data = body.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"] is not None:
        a.slug = data["slug"]
    if data.get("kind") is not None:
        a.kind = data["kind"]
    if data.get("category") is not None:
        a.category = data["category"]
    if data.get("title_i18n") is not None:
        a.title_i18n = _full(data["title_i18n"])
    if data.get("body_i18n") is not None:
        a.body_i18n = _full(data["body_i18n"])
        a.body_html_i18n = render_i18n(a.body_i18n)
    if data.get("pinned") is not None:
        a.pinned = data["pinned"]
    if data.get("status") is not None:
        if data["status"] == "published" and a.published_at is None:
            a.published_at = datetime.now(timezone.utc)
        a.status = data["status"]
    a.updated_at = datetime.now(timezone.utc)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("An article with this slug already exists") from None
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="article.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"article:{a.id}",
        before=before,
        after={"slug": a.slug, "status": a.status, "pinned": a.pinned},
        ip=ip,
        detail={"fields": sorted(data)},
    )
    await db.commit()
    await db.refresh(a)
    return a


async def admin_delete(
    db: AsyncSession, admin: User, article_id: uuid.UUID, ip: str | None
) -> None:
    a = await admin_get(db, article_id, lock=True)
    if a.kind == "page":
        raise Validation("Fixed pages cannot be deleted; unpublish them instead")
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="article.deleted",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"article:{a.id}",
        before={"slug": a.slug, "status": a.status},
        ip=ip,
    )
    await db.delete(a)
    await db.commit()
