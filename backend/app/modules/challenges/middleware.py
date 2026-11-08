"""Gate catalog and crawler data at the server while leaving recovery routes operational."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.core.db import tenant_session
from app.core.errors import ApiError, error_body
from app.core.middleware import ASGIApp, Message, Receive, Scope, Send, _send_json
from app.modules.challenges import service
from app.modules.content.site import tenant_settings
from app.modules.seo import template

API_DATA = (
    "/api/v1/products",
    "/api/v1/categories",
    "/api/v1/catalog",
    "/api/v1/articles",
    "/api/v1/support/status",
)
HTML_ENTRY = ("/p/", "/a/", "/c/")
HTML_EXACT = frozenset({"/", "/help", "/search", "/sitemap.xml", "/robots.txt"})


def protected(path: str) -> bool:
    return (
        path in HTML_EXACT
        or any(path == p or path.startswith(p + "/") for p in API_DATA)
        or path.startswith(HTML_ENTRY)
    )


class SiteVisitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "HEAD")
            or not protected(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        res = scope["app"].state.res
        try:
            tenant = await res.tenants.resolve(request.headers.get("host"))
            async with tenant_session(res.maker, tenant) as db:
                ts = await tenant_settings(db, tenant)
            enabled = service.policy(ts)["site_visit"]
            blocked = enabled and not service.clearance(request, res, tenant, ts)
        except ApiError as exc:
            await _send_json(send, exc.status, error_body(exc.code, exc.message))
            return
        except Exception:
            await _send_json(
                send,
                503,
                error_body("unavailable", "Entry verification unavailable"),
                [(b"cache-control", b"no-store")],
            )
            return
        if not blocked:

            async def private_send(message: Message) -> None:
                if enabled and message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    vary = next((value for key, value in headers if key.lower() == b"vary"), b"")
                    headers = [
                        (key, value)
                        for key, value in headers
                        if key.lower() not in (b"cache-control", b"vary")
                    ]
                    headers.extend(
                        [
                            (b"cache-control", b"private, no-store"),
                            (b"vary", vary + b", Cookie" if vary else b"Cookie"),
                        ]
                    )
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, private_send)
            return
        if scope["path"].startswith("/api/"):
            await _send_json(
                send,
                428,
                error_body("site_visit_required", "Entry verification required"),
                [(b"cache-control", b"no-store"), (b"vary", b"Cookie")],
            )
        else:
            # Render only the public SPA shell. No catalog data, JSON-LD or crawler content
            # enters the response before clearance; the global widget can still mount.
            shell = await template.load_template(res)
            response = HTMLResponse(
                shell or "Entry verification required / 请先完成访问验证",
                status_code=428 if shell else 503,
                headers={
                    "Cache-Control": "no-store",
                    "Vary": "Cookie",
                    "X-Robots-Tag": "noindex, nofollow",
                },
            )
            await response(scope, receive, send)
