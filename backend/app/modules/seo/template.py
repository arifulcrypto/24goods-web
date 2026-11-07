"""The SPA shell (``index.html``) the SEO pages are rendered into.

Source: ``APP_WEB_INDEX_PATH`` (a file, e.g. a bind mount or dev build) else
``APP_WEB_INDEX_URL`` (default ``http://web/index.html``: Caddy's web container on the internal
edge network, see the ``http://web`` site block in deploy/Caddyfile). Cached in memory for
``TTL_S``; on a fetch error the last good copy keeps being served (stale-if-error).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from app.core.resources import Resources

log = logging.getLogger("app.seo")
TTL_S = 60.0
MAX_BYTES = 512 * 1024
STALE_MAX_S = 3600.0  # a stale copy older than this is dropped (shell would reference gone assets)
_cache: tuple[float, str] | None = None
_good_at = 0.0


def reset() -> None:
    global _cache, _good_at
    _cache, _good_at = None, 0.0


def _valid(doc: str) -> bool:
    return '<div id="app"></div>' in doc and "</head>" in doc


async def load_template(res: Resources) -> str | None:
    """The shell, or None when it was never available (the caller answers 503; Caddy then
    falls back to the static file)."""
    global _cache, _good_at
    now = time.monotonic()
    if _cache is not None and _cache[0] > now:
        return _cache[1]
    s = res.settings
    doc: str | None = None
    try:
        if s.web_index_path:
            data = await asyncio.to_thread(Path(s.web_index_path).read_bytes)
            doc = data[:MAX_BYTES].decode("utf-8")
        else:
            async with res.http.stream("GET", s.web_index_url, timeout=3.0) as r:
                if r.status_code == 200:
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > MAX_BYTES:
                            raise ValueError("template too large")
                    doc = buf.decode("utf-8")
    except Exception as exc:
        log.warning("seo template fetch failed error=%s", type(exc).__name__)
    if doc is not None and _valid(doc):
        _cache, _good_at = (now + TTL_S, doc), now
        return doc
    if (
        _cache is not None and now - _good_at < STALE_MAX_S
    ):  # stale-if-error: retry soon, keep serving the old shell
        _cache = (now + 10.0, _cache[1])
        return _cache[1]
    return None
