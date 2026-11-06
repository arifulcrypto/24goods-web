"""Admin product import endpoints (review-v5 §8): SSRF-safe fetch → parse → draft; image fetch."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import safefetch
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.models import Tenant, User
from app.modules.catalog import importer
from app.modules.common.views import file_url
from app.modules.uploads.processing import process_image
from app.modules.uploads.service import _image_slot, store_attachment
from app.modules.uploads.storage import FileStore

router = APIRouter(prefix="/admin", tags=["admin-import"])

# test seams (monkeypatched offline): DNS resolver and httpx transport
_resolver: safefetch.Resolver | None = None
_transport: Any = None


class FetchIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ParseIn(BaseModel):
    text: str | None = Field(default=None, max_length=300_000)
    html: str | None = Field(default=None, max_length=safefetch.PAGE_MAX_BYTES)
    base_url: str | None = Field(default=None, max_length=2000)


async def _import_cfg(db: AsyncSession, admin: User) -> dict[str, Any]:
    ts = await db.scalar(select(Tenant.settings).where(Tenant.id == admin.tenant_id))
    return section(ts, "import")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "")[:253]


@router.post("/products/import/fetch")
async def import_fetch(
    body: FetchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "import:fetch", str(admin.id), 30, 60)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="product.import_fetch",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"host:{_host(body.url)}",
        ip=client_ip(request),
    )
    await db.commit()  # keep the audit row even when the fetch is refused
    cfg = await _import_cfg(db, admin)
    return await importer.import_from_url(body.url, cfg, resolver=_resolver, transport=_transport)


@router.post("/products/import/parse")
async def import_parse(
    body: ParseIn,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "import:fetch", str(admin.id), 30, 60)
    if not (body.html and body.html.strip()) and not (body.text and body.text.strip()):
        raise Validation("text or html is required")
    cfg = await _import_cfg(db, admin)
    return importer.parse_pasted(text=body.text, html=body.html, base_url=body.base_url, cfg=cfg)


@router.post("/content/images/fetch")
async def import_image_fetch(
    body: FetchIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Download a remote image through the SSRF-safe fetcher, then the normal hostile-upload
    pipeline (magic bytes, re-encode, EXIF strip). Same response as ``POST /content/images``."""
    await enforce(res.redis, "import:fetch", str(admin.id), 30, 60)
    got = await safefetch.fetch_image(body.url, resolver=_resolver, transport=_transport)
    async with _image_slot:
        processed = await anyio.to_thread.run_sync(process_image, got.body)
    att = await store_attachment(
        db,
        FileStore(res.settings.upload_dir),
        tenant_id=admin.tenant_id,
        owner_id=admin.id,
        kind="image",
        processed=processed,
        purpose="product",
        is_public=True,
    )
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="content.image_imported",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"attachment:{att.id}",
        ip=client_ip(request),
        detail={"host": _host(got.url)},
    )
    await db.commit()
    return {"id": str(att.id), "url": file_url(att.id)}
