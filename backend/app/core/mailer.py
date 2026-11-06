"""SMTP sender (stdlib ``smtplib`` in a worker thread; no new dependency).

The password is never logged. Errors are reduced to a short code before they leave this
module (``connect_failed``, ``auth_failed``, ``rejected``, ``send_failed``).
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox
from app.core.config import Settings
from app.core.settings_v5 import section

TIMEOUT_S = 10


class MailError(Exception):
    """Sanitized failure; ``str(exc)`` is one of the short codes (safe to show an admin)."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    from_name: str
    security: str  # starttls | ssl | none


def build_message(
    cfg: SmtpConfig, to: str, subject: str, body: str, html: str | None = None
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_addr)) if cfg.from_name else cfg.from_addr
    msg["To"] = to
    msg["Subject"] = " ".join(subject.split())[:200]
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_sync(cfg: SmtpConfig, msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    if cfg.security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=TIMEOUT_S, context=ctx)
    else:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=TIMEOUT_S)
    with client:
        if cfg.security == "starttls":
            client.starttls(context=ctx)
        if cfg.username:
            client.login(cfg.username, cfg.password)
        client.send_message(msg)


async def send_mail(
    cfg: SmtpConfig, to: str, subject: str, body: str, html: str | None = None
) -> None:
    """Send one plain-text mail; raises :class:`MailError` with a sanitized code."""
    try:
        msg = build_message(cfg, to, subject, body, html)
        await asyncio.to_thread(_send_sync, cfg, msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("auth_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise MailError("rejected") from None
    except (TimeoutError, OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
        raise MailError("connect_failed") from None
    except (smtplib.SMTPException, ValueError):
        raise MailError("send_failed") from None


async def load_config(
    db: AsyncSession, settings: Settings, tenant_id: uuid.UUID, ts: dict, *, require_enabled: bool
) -> SmtpConfig | None:
    em = section(ts, "email")
    if require_enabled and not em["enabled"]:
        return None
    if not em["host"] or not em["from_addr"]:
        return None
    password = await secretbox.get_secret(db, settings, tenant_id, "smtp_password") or ""
    return SmtpConfig(
        host=em["host"],
        port=em["port"],
        username=em["username"],
        password=password,
        from_addr=em["from_addr"],
        from_name=em["from_name"],
        security=em["security"],
    )
