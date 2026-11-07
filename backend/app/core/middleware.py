"""Pure-ASGI middlewares (streaming/SSE safe): request id, client IP, security
headers, CSRF header check, body size limits and a query-string-free access log."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

from starlette.exceptions import HTTPException

from app.core.config import Network
from app.core.errors import error_body

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

access_log = logging.getLogger("app.access")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class BodyTooLarge(HTTPException):
    """Raised from ``receive``; FastAPI re-raises HTTPException from body parsing."""

    def __init__(self, code: str):
        super().__init__(413, "Request body too large")
        self.code = code


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


async def _send_json(send: Send, status: int, body: dict[str, Any], extra: Iterable = ()) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def client_ip_from(peer: str | None, xff: str | None, trusted: tuple[Network, ...]) -> str | None:
    """Right-most untrusted address in X-Forwarded-For, only if the peer is trusted."""

    def is_trusted(addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in net for net in trusted)

    if peer is None or not xff or not is_trusted(peer):
        return peer
    hops = [h.strip() for h in xff.split(",") if h.strip()][-20:]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # malformed chain: do not trust any of it
        if not is_trusted(hop):
            return hop
    return hops[0] if hops else peer


class CoreMiddleware:
    """Request id + client IP + security headers + access log (one wrapper, less overhead)."""

    def __init__(self, app: ASGIApp, trusted: tuple[Network, ...]):
        self.app = app
        self.trusted = trusted

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _header(scope, b"x-request-id")
        if not rid or not _REQ_ID.match(rid):
            rid = secrets.token_hex(8)
        peer = scope.get("client")[0] if scope.get("client") else None
        ip = client_ip_from(peer, _header(scope, b"x-forwarded-for"), self.trusted)
        from app.core.audit import api_token_ctx

        api_token_ctx.set(None)  # never inherit a previous request's token attribution
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        state["client_ip"] = ip
        started = time.perf_counter()
        status_holder = [0]

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder[0] = message["status"]
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS.items():
                    if k.lower().encode() not in present:
                        headers.append((k.lower().encode(), v.encode()))
                if b"cache-control" not in present and scope.get("path", "").startswith("/api/"):
                    # API responses are per-user and may carry secrets: never cache by default.
                    headers.append((b"cache-control", b"no-store"))
                headers.append((b"x-request-id", rid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            # Path only: query strings may carry gateway parameters.
            access_log.info(
                "%s %s %s %.1fms rid=%s",
                scope.get("method"),
                scope.get("path"),
                status_holder[0],
                (time.perf_counter() - started) * 1000,
                rid,
            )


def _is_token_request(scope: Scope) -> bool:
    """API-token call: kst_ bearer on a token-eligible path and NO session cookie.

    Browsers attach cookies ambiently, tokens never; so CSRF protection is only needed when a
    cookie is present. Bearer tokens are re-validated in the auth dependency.
    """
    from app.core.apitoken import TOKEN_PREFIX, path_allows_token
    from app.core.security import COOKIE_NAME, SECURE_COOKIE_NAME

    auth = _header(scope, b"authorization") or ""
    if auth[:7].lower() != "bearer " or not auth[7:].strip().startswith(TOKEN_PREFIX):
        return False
    if not path_allows_token(scope["path"]):
        return False
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            names = {c.split("=", 1)[0].strip() for c in v.decode("latin-1").split(";")}
            if names & {COOKIE_NAME, SECURE_COOKIE_NAME}:
                return False
    return True


class CSRFMiddleware:
    """Unsafe methods under /api/ must carry ``X-Requested-With: fetch``.

    A cross-site form or simple fetch cannot set custom headers without a CORS
    preflight, which this API never grants. Gateway notify callbacks and cookie-less API-token
    requests are exempt. The access log records method, path and status only: never headers.
    """

    def __init__(self, app: ASGIApp, exempt: Iterable[str] = ()):
        self.app = app
        self.exempt = frozenset(exempt)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in UNSAFE_METHODS
            and scope["path"].startswith("/api/")
            and scope["path"] not in self.exempt
            and _header(scope, b"x-requested-with") != "fetch"
            and not _is_token_request(scope)
        ):
            await _send_json(send, 403, error_body("csrf", "Missing or invalid CSRF header"))
            return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Reject oversized bodies early (declared or streamed)."""

    def __init__(
        self, app: ASGIApp, default_limit: int, upload_limit: int, upload_paths: re.Pattern[str]
    ):
        self.app = app
        self.default = default_limit
        self.upload = upload_limit
        self.upload_paths = upload_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        is_upload = bool(self.upload_paths.match(scope["path"]))
        limit = self.upload if is_upload else self.default
        code = "upload_rejected" if is_upload else "validation"
        declared = _header(scope, b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                await _send_json(send, 413, error_body(code, "Request body too large"))
                return
        received = 0
        started = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        async def _receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(code)
            return message

        try:
            await self.app(scope, _receive, _send)
        except BodyTooLarge:
            if not started:
                await _send_json(send, 413, error_body(code, "Request body too large"))
