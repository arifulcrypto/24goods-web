"""Settings center endpoints that touch secrets or send mail (review-v5 §3, §5.2)."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import email_templates, mailer, notify_tg, secretbox
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.content.site import tenant_settings

router = APIRouter(prefix="/admin/site", tags=["admin"])

_EMAIL = re.compile(r"^[^@\s<>]{1,64}@[A-Za-z0-9.-]{1,253}$")


class SecretIn(BaseModel):
    name: Literal[
        "turnstile_secret",
        "google_client_secret",
        "smtp_password",
        "telegram_bot_token",
        "cloudflare_saas_token",
    ]
    value: str | None = Field(None, max_length=512)  # null deletes

    @field_validator("value")
    @classmethod
    def _v(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip() or any(ord(c) < 32 for c in v):
            raise ValueError("value must be non-empty text without control characters")
        return v


class EmailTestIn(BaseModel):
    to: str = Field(max_length=254)
    template: Literal["verify_code", "password_reset", "order_paid", "order_delivered"] | None = (
        None
    )
    locale: Literal["en", "zh-CN"] = "en"

    @field_validator("to")
    @classmethod
    def _t(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL.match(v):
            raise ValueError("invalid email")
        return v


@router.put("/secrets")
async def put_secret(
    body: SecretIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Write-only: the response carries ``configured`` + ``last4`` per secret, never a value.
    The audit row names the secret, never its content."""
    await enforce(res.redis, "settings:secret", str(admin.id), 20, 3600)
    await secretbox.set_secret(db, res.settings, admin.tenant_id, body.name, body.value, admin.id)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="settings.secret_set",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"secret:{body.name}",
        ip=client_ip(request),
        detail={"name": body.name, "cleared": body.value is None},
    )
    await db.commit()
    return {"secrets": await secretbox.status(db, admin.tenant_id)}


@router.post("/email/test")
async def email_test(
    body: EmailTestIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "settings:mailtest", str(admin.id), 5, 600)
    ts = await tenant_settings(db, admin.tenant_id)
    cfg = await mailer.load_config(db, res.settings, admin.tenant_id, ts, require_enabled=False)
    if cfg is None:
        raise Validation("SMTP host and from address must be configured first")
    ok, error = True, None
    subject, text, html = "Test email", "This is a test email from your store settings.", None
    if body.template:  # sample render of the configured template (placeholders filled)
        subject, text, html = email_templates.render_template(
            ts,
            body.template,
            body.locale,
            {**email_templates.SAMPLE, "site_name": email_templates.site_name(ts, body.locale)},
        )
    try:
        await mailer.send_mail(cfg, body.to, subject, text, html)
    except mailer.MailError as exc:
        ok, error = False, str(exc)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="settings.email_test",
        actor_id=admin.id,
        actor_role=admin.role,
        target="email:test",
        ip=client_ip(request),
        detail={"ok": ok, "error": error},
        severity="info" if ok else "warning",
    )
    await db.commit()
    return {"ok": ok, **({"error": error} if error else {})}


@router.post("/telegram/test")
async def telegram_test(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Send a test message to the configured chats (ignores the enable/event toggles)."""
    await enforce(res.redis, "settings:tgtest", str(admin.id), 5, 600)
    ok, error = await notify_tg.send_test(res, admin.tenant_id)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="settings.telegram_test",
        actor_id=admin.id,
        actor_role=admin.role,
        target="telegram:test",
        ip=client_ip(request),
        detail={"ok": ok, "error": error},
        severity="info" if ok else "warning",
    )
    await db.commit()
    return {"ok": ok, **({"error": error} if error else {})}
