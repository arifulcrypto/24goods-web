"""Reseller custom domains through Cloudflare for SaaS (docs/cloudflare-reseller-domains.md).

Tenant-scoped (RLS + explicit filters), owner-reseller only, every change audited. Only an
``active`` row resolves a storefront (``app/core/storefront.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox, storefront
from app.core.audit import audit
from app.core.cf_saas import CFError, CFSaaS
from app.core.errors import ApiError, Conflict, NotFound, Validation
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.internal import normalize_hostname
from app.models import Reseller, ResellerDomain, User
from app.modules.content.site import tenant_settings


class Unavailable(ApiError):
    """Custom domains are not available"""

    status, code = 409, "custom_domains_unavailable"


class CloudflareFailed(ApiError):
    """Domain provider error"""

    status, code = 502, "domain_provider_error"


def _under(host: str, base: str) -> bool:
    return bool(base) and (host == base or host.endswith("." + base))


def check_hostname(raw: str, rcfg: dict[str, Any], public_host: str) -> str:
    """Normalized hostname or Validation. No IPs, no platform hosts, no apex of the base domain."""
    host = normalize_hostname(raw)
    if host is None:
        raise Validation("Enter a valid domain name such as shop.example.com", code="bad_hostname")
    base = str(rcfg["base_domain"]).lower()
    origin = str(rcfg["fallback_origin"]).lower()
    ph = public_host.lower()
    if _under(host, base) or _under(host, origin) or host == ph or host.endswith("." + ph):
        raise Validation("This domain belongs to the platform", code="hostname_reserved")
    return host


async def client_for(
    db: AsyncSession, res: Resources, tenant_id: uuid.UUID, rcfg: dict[str, Any]
) -> CFSaaS:
    """The CF client, or 409 custom_domains_unavailable when the feature is not fully configured."""
    if not rcfg["custom_domains_enabled"] or not rcfg["cf_zone_id"]:
        raise Unavailable("Custom domains are not available")
    token = await secretbox.get_secret(db, res.settings, tenant_id, "cloudflare_saas_token")
    if not token:
        raise Unavailable("Custom domains are not available")
    return CFSaaS(res.http, rcfg["cf_zone_id"], token)


def view(d: ResellerDomain) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "hostname": d.hostname,
        "status": d.status,
        "ssl_status": d.ssl_status,
        "verification": d.verification or {},
        "created_at": d.created_at,
        "verified_at": d.verified_at,
    }


async def list_mine(db: AsyncSession, r: Reseller, rcfg: dict[str, Any]) -> dict[str, Any]:
    rows = (
        await db.scalars(
            select(ResellerDomain)
            .where(ResellerDomain.reseller_id == r.id, ResellerDomain.tenant_id == r.tenant_id)
            .order_by(ResellerDomain.created_at)
        )
    ).all()
    return {
        "enabled": bool(rcfg["custom_domains_enabled"] and rcfg["cf_zone_id"]),
        "fallback_origin": rcfg["fallback_origin"],
        "max_domains": rcfg["max_domains"],
        "items": [view(d) for d in rows],
    }


async def _get_mine(db: AsyncSession, r: Reseller, did: uuid.UUID) -> ResellerDomain:
    d = await db.scalar(
        select(ResellerDomain).where(
            ResellerDomain.id == did,
            ResellerDomain.reseller_id == r.id,
            ResellerDomain.tenant_id == r.tenant_id,
        )
    )
    if d is None:
        raise NotFound("Domain not found")
    return d


async def add(
    db: AsyncSession, res: Resources, user: User, r: Reseller, raw: str, ip: str | None
) -> ResellerDomain:
    tid = user.tenant_id
    rcfg = section(await tenant_settings(db, tid), "resellers")
    cf = await client_for(db, res, tid, rcfg)
    from urllib.parse import urlsplit

    host = check_hostname(raw, rcfg, urlsplit(res.settings.public_base_url).hostname or "")
    n = await db.scalar(
        select(func.count())
        .select_from(ResellerDomain)
        .where(ResellerDomain.reseller_id == r.id, ResellerDomain.tenant_id == tid)
    )
    if int(n or 0) >= int(rcfg["max_domains"]):
        raise Conflict("Domain limit reached", code="domain_limit")
    if await db.scalar(select(ResellerDomain.id).where(ResellerDomain.hostname == host)):
        raise Conflict("This domain is already in use", code="domain_taken")
    try:
        ch = await cf.create(host)
    except CFError as exc:
        raise CloudflareFailed(str(exc)) from None
    d = ResellerDomain(
        tenant_id=tid,
        reseller_id=r.id,
        hostname=host,
        cf_id=ch.cf_id,
        status=ch.app_status,
        ssl_status=ch.ssl_status,
        verification=ch.verification,
    )
    db.add(d)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("This domain is already in use", code="domain_taken") from None
    await audit(
        db,
        tenant_id=tid,
        action="reseller.domain_added",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller_domain:{d.id}",
        after={"hostname": host, "cf_id": ch.cf_id, "status": d.status},
        ip=ip,
    )
    await db.commit()
    storefront.invalidate()
    return d


async def refresh(
    db: AsyncSession, res: Resources, user: User, r: Reseller, did: uuid.UUID, ip: str | None
) -> ResellerDomain:
    d = await _get_mine(db, r, did)
    rcfg = section(await tenant_settings(db, user.tenant_id), "resellers")
    cf = await client_for(db, res, user.tenant_id, rcfg)
    if not d.cf_id:
        raise Conflict("Domain has no provider id", code="domain_no_cf_id")
    try:
        ch = await cf.get(d.cf_id)
    except CFError as exc:
        raise CloudflareFailed(str(exc)) from None
    before = {"status": d.status, "ssl_status": d.ssl_status}
    d.status, d.ssl_status, d.verification = ch.app_status, ch.ssl_status, ch.verification
    if d.status == "active" and d.verified_at is None:
        d.verified_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="reseller.domain_refreshed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller_domain:{d.id}",
        before=before,
        after={"status": d.status, "ssl_status": d.ssl_status},
        ip=ip,
    )
    await db.commit()
    storefront.invalidate()
    return d


async def remove(
    db: AsyncSession,
    res: Resources,
    user: User,
    d: ResellerDomain,
    ip: str | None,
    *,
    force: bool = False,
) -> None:
    """Delete at Cloudflare, then the row. ``force`` (staff) drops the row even if CF fails."""
    rcfg = section(await tenant_settings(db, user.tenant_id), "resellers")
    if d.cf_id:
        try:
            cf = await client_for(db, res, user.tenant_id, rcfg)
            await cf.delete(d.cf_id)
        except (CFError, Unavailable) as exc:
            if not force and not (isinstance(exc, CFError) and "404" in str(exc)):
                raise CloudflareFailed(str(exc)) from None
    await db.execute(
        delete(ResellerDomain).where(
            ResellerDomain.id == d.id, ResellerDomain.tenant_id == user.tenant_id
        )
    )
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="reseller.domain_removed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller_domain:{d.id}",
        before={"hostname": d.hostname, "status": d.status},
        ip=ip,
        detail={"force": force},
    )
    await db.commit()
    storefront.invalidate()


async def admin_list(db: AsyncSession, staff: User) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(ResellerDomain, Reseller.slug)
            .join(Reseller, Reseller.id == ResellerDomain.reseller_id)
            .where(ResellerDomain.tenant_id == staff.tenant_id)
            .order_by(ResellerDomain.created_at.desc())
            .limit(500)
        )
    ).all()
    return [{**view(d), "reseller_slug": slug} for d, slug in rows]


async def admin_get(db: AsyncSession, staff: User, did: uuid.UUID) -> ResellerDomain:
    d = await db.scalar(
        select(ResellerDomain).where(
            ResellerDomain.id == did, ResellerDomain.tenant_id == staff.tenant_id
        )
    )
    if d is None:
        raise NotFound("Domain not found")
    return d
