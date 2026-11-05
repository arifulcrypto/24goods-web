"""Passwords (Argon2id) and server-side sessions (Redis, hashed token keys)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import anyio.to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from redis.asyncio import Redis
from starlette.responses import Response

from app.core.config import Settings

COOKIE_NAME = "sid"  # plain-HTTP demo only
SECURE_COOKIE_NAME = "__Host-sid"  # browsers enforce Secure, Path=/ and no Domain
STAFF_ROLES = frozenset({"agent", "admin"})

# OWASP Password Storage Cheat Sheet Argon2id profile m=12 MiB, t=3, p=1 (equivalent
# strength to m=19 MiB/t=2): bounded per-hash memory for small hosts. Existing hashes with
# other parameters still verify and are transparently upgraded on login (check_needs_rehash).
_hasher = PasswordHasher(time_cost=3, memory_cost=12288, parallelism=1, hash_len=32, salt_len=16)
_hash_slots = asyncio.Semaphore(2)
_dummy_hash: str | None = None


async def hash_password(password: str) -> str:
    async with _hash_slots:
        return await anyio.to_thread.run_sync(_hasher.hash, password)


async def verify_password(pw_hash: str | None, password: str) -> tuple[bool, bool]:
    """Returns (ok, needs_rehash). Runs a dummy verify for unknown users (timing)."""
    global _dummy_hash
    if pw_hash is None:
        if _dummy_hash is None:
            _dummy_hash = await hash_password(secrets.token_urlsafe(16))
        pw_hash = _dummy_hash
        known = False
    else:
        known = True

    def _verify() -> bool:
        try:
            return _hasher.verify(pw_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    async with _hash_slots:
        ok = await anyio.to_thread.run_sync(_verify)
    ok = ok and known
    return ok, bool(ok and _hasher.check_needs_rehash(pw_hash))


@dataclass(frozen=True)
class SessionData:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    created_at: int


def _key(token: str) -> str:
    # Only the SHA-256 of the cookie is stored: a Redis dump does not yield live sessions.
    return "sess:" + hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings):
        self._redis = redis
        self._settings = settings

    @property
    def cookie_name(self) -> str:
        return SECURE_COOKIE_NAME if self._settings.cookie_secure else COOKIE_NAME

    def token_from(self, cookies: Mapping[str, str]) -> str | None:
        return cookies.get(self.cookie_name)

    def ttl_for(self, role: str) -> int:
        s = self._settings
        return s.session_ttl_staff_s if role in STAFF_ROLES else s.session_ttl_buyer_s

    async def create(self, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
        token = secrets.token_urlsafe(32)  # 256-bit
        data = {"uid": str(user_id), "tid": str(tenant_id), "role": role, "iat": int(time.time())}
        await self._redis.set(_key(token), json.dumps(data), ex=self.ttl_for(role))
        return token

    async def get(self, token: str | None) -> SessionData | None:
        if not token or len(token) > 128:
            return None
        raw = await self._redis.get(_key(token))
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return SessionData(uuid.UUID(d["uid"]), uuid.UUID(d["tid"]), d["role"], int(d["iat"]))
        except (ValueError, KeyError, TypeError):
            return None

    async def delete(self, token: str | None) -> None:
        if token and len(token) <= 128:
            await self._redis.delete(_key(token))

    def set_cookie(self, response: Response, token: str, role: str) -> None:
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=self.ttl_for(role),
            httponly=True,
            samesite="lax",
            secure=self._settings.cookie_secure,
            path="/",
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self.cookie_name,
            path="/",
            httponly=True,
            samesite="lax",
            secure=self._settings.cookie_secure,
        )
