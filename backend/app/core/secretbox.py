"""AES-256-GCM sealing of tenant setting secrets (SMTP password, Turnstile / Google secrets).

Key: ``APP_SETTINGS_KEY`` (32 bytes, api.env only). Nonce: random 12 bytes, stored in front of
the ciphertext. AAD binds a row to ``tenant_id|name`` so a ciphertext cannot be moved to
another tenant or secret name. Plaintext is never logged, audited or returned: callers get
``configured`` + ``last4`` only (:func:`status`) and the decrypted value only through
:func:`get_secret` inside the process that needs it.
"""

from __future__ import annotations

import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.settings_v5 import SECRET_NAMES
from app.models.growth import TenantSecret

NONCE_LEN = 12


def _aad(tenant_id: uuid.UUID, name: str) -> bytes:
    return f"{tenant_id}|{name}".encode()


def seal(key: bytes, tenant_id: uuid.UUID, name: str, value: str) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    return nonce + AESGCM(key).encrypt(nonce, value.encode(), _aad(tenant_id, name))


def unseal(key: bytes, tenant_id: uuid.UUID, name: str, blob: bytes) -> str | None:
    """Plaintext, or ``None`` when the blob does not authenticate (wrong key / tenant / name)."""
    if len(blob) <= NONCE_LEN:
        return None
    try:
        return (
            AESGCM(key).decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], _aad(tenant_id, name)).decode()
        )
    except (InvalidTag, UnicodeDecodeError):
        return None


def _check_name(name: str) -> None:
    if name not in SECRET_NAMES:
        raise ValueError("unknown secret name")


async def set_secret(
    db: AsyncSession,
    settings: Settings,
    tenant_id: uuid.UUID,
    name: str,
    value: str | None,
    updated_by: uuid.UUID | None = None,
) -> None:
    """Store (or, with ``None``, delete) a secret. Caller commits."""
    _check_name(name)
    if value is None:
        await db.execute(
            delete(TenantSecret).where(
                TenantSecret.tenant_id == tenant_id, TenantSecret.name == name
            )
        )
        return
    blob = seal(settings.settings_secret(), tenant_id, name, value)
    stmt = pg_insert(TenantSecret).values(
        tenant_id=tenant_id, name=name, ciphertext=blob, last4=value[-4:], updated_by=updated_by
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "name"],
            set_={
                "ciphertext": stmt.excluded.ciphertext,
                "last4": stmt.excluded.last4,
                "updated_by": stmt.excluded.updated_by,
                "updated_at": func.now(),
            },
        )
    )


async def get_secret(
    db: AsyncSession, settings: Settings, tenant_id: uuid.UUID, name: str
) -> str | None:
    _check_name(name)
    blob = await db.scalar(
        select(TenantSecret.ciphertext).where(
            TenantSecret.tenant_id == tenant_id, TenantSecret.name == name
        )
    )
    if blob is None:
        return None
    return unseal(settings.settings_secret(), tenant_id, name, blob)


async def status(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, dict[str, object]]:
    """``{name: {configured, last4}}`` for every known secret (never the value)."""
    rows = {
        n: l4
        for n, l4 in (
            await db.execute(
                select(TenantSecret.name, TenantSecret.last4).where(
                    TenantSecret.tenant_id == tenant_id
                )
            )
        ).all()
    }
    return {
        n: {"configured": n in rows, "last4": rows.get(n) if n in rows else None}
        for n in SECRET_NAMES
    }
