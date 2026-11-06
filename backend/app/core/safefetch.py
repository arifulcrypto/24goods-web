"""SSRF-safe HTTP fetcher (review-v5 §8.1).

Used only by admin-triggered imports. Every hop (including redirects) is vetted: scheme,
port, no userinfo, DNS-resolved addresses must all be globally routable. The connection is
then *pinned* to the vetted IP (no second DNS lookup, so no rebinding window) while the Host
header and TLS SNI / certificate verification keep using the hostname. No cookies, no env
proxies, no auth forwarding, 10 s total, streamed body caps.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.errors import ApiError

USER_AGENT = "KesivImporter/1.0"
ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_REDIRECTS = 3
TOTAL_TIMEOUT_S = 10.0
PAGE_MAX_BYTES = 3 * 1024 * 1024
IMAGE_MAX_BYTES = 8 * 1024 * 1024
PAGE_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
REDIRECT_CODES = {301, 302, 303, 307, 308}

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(ApiError):
    """Could not fetch the URL"""

    status, code = 422, "fetch_failed"


class UnsafeURL(FetchError):
    """URL not allowed"""

    code = "unsafe_url"


@dataclass(frozen=True)
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- vetting
def ip_allowed(addr: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(addr) if isinstance(addr, str) else addr
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return False  # ::ffff:a.b.c.d
        if ip.sixtofour is not None:  # 2002::/16 embeds an IPv4
            return ip_allowed(ip.sixtofour)
        if ip in ipaddress.ip_network("64:ff9b::/96"):  # NAT64 embeds an IPv4
            return ip_allowed(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in ipaddress.ip_network("2001::/32"):  # Teredo
            return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    ):
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return bool(ip.is_global)


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise FetchError("host could not be resolved") from e
    return list(dict.fromkeys(i[4][0] for i in infos))


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str  # ASCII hostname (or literal IP)
    port: int
    path: str
    ip: str  # vetted address to connect to


async def vet_url(url: str, resolver: Resolver | None = None) -> Target:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL("only http/https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in URL are not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURL("URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeURL("bad port") from e
    if port not in ALLOWED_PORTS:
        raise UnsafeURL("port not allowed")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise UnsafeURL("bad host name") from e
    try:
        literal = ipaddress.ip_address(host)
        addrs = [str(literal)]
    except ValueError:
        if host.lower() == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise UnsafeURL("host not allowed") from None
        addrs = await (resolver or _system_resolver)(host, port)
    if not addrs:
        raise FetchError("host could not be resolved")
    for a in addrs:  # every record must be public (defeats mixed A records)
        if not ip_allowed(a):
            raise UnsafeURL("host resolves to a non-public address")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(url, parts.scheme, host, port, path, addrs[0])


# ---------------------------------------------------------------- fetching
def _pinned_url(t: Target) -> str:
    ip = ipaddress.ip_address(t.ip)
    h = f"[{ip}]" if ip.version == 6 else str(ip)
    default = 443 if t.scheme == "https" else 80
    return f"{t.scheme}://{h}{'' if t.port == default else f':{t.port}'}{t.path}"


async def _hop(
    t: Target,
    *,
    accept: str,
    max_bytes: int,
    types: tuple[str, ...],
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[int, str, bytes, str | None]:
    default = 443 if t.scheme == "https" else 80
    host_header = t.host if t.port == default else f"{t.host}:{t.port}"
    headers = {"Host": host_header, "User-Agent": USER_AGENT, "Accept": accept}
    ext = {"sni_hostname": t.host} if t.scheme == "https" else {}
    # fresh client per hop: no cookie jar carry-over, no env proxies, no auto redirects
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=False, trust_env=False, timeout=TOTAL_TIMEOUT_S
    ) as client:
        async with client.stream("GET", _pinned_url(t), headers=headers, extensions=ext) as r:
            loc = r.headers.get("location")
            if r.status_code in REDIRECT_CODES and loc:
                return r.status_code, "", b"", loc
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if r.status_code >= 400:
                raise FetchError(f"remote answered HTTP {r.status_code}")
            if not any(ctype == x or (x.endswith("/") and ctype.startswith(x)) for x in types):
                raise FetchError("unsupported content type")
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FetchError("response too large")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise FetchError("response too large")
            return r.status_code, ctype, bytes(buf), None


async def _fetch(
    url: str,
    *,
    accept: str,
    max_bytes: int,
    types: tuple[str, ...],
    resolver: Resolver | None,
    transport: httpx.AsyncBaseTransport | None,
) -> Fetched:
    async def run() -> Fetched:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            t = await vet_url(current, resolver)
            status, ctype, body, loc = await _hop(
                t, accept=accept, max_bytes=max_bytes, types=types, transport=transport
            )
            if loc is None:
                return Fetched(current, status, ctype, body)
            current = urljoin(current, loc)
        raise FetchError("too many redirects")

    try:
        return await asyncio.wait_for(run(), TOTAL_TIMEOUT_S)
    except TimeoutError as e:
        raise FetchError("timed out") from e
    except asyncio.TimeoutError as e:  # py3.10
        raise FetchError("timed out") from e
    except httpx.HTTPError as e:
        raise FetchError("could not connect to the remote host") from e


async def fetch_page(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    accept: str = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
) -> Fetched:
    return await _fetch(
        url,
        accept=accept,
        max_bytes=PAGE_MAX_BYTES,
        types=PAGE_TYPES,
        resolver=resolver,
        transport=transport,
    )


async def fetch_image(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Fetched:
    return await _fetch(
        url,
        accept="image/*",
        max_bytes=IMAGE_MAX_BYTES,
        types=("image/",),
        resolver=resolver,
        transport=transport,
    )
