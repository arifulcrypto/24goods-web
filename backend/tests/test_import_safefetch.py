"""SSRF vetting for the import fetcher (offline: fake DNS + mock transport)."""

from __future__ import annotations

import httpx
import pytest

from app.core import safefetch as sf


def resolver_for(table: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        return table[host]

    return resolve


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.9.9",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.127.255.254",
        "0.0.0.0",  # noqa: S104
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "::ffff:127.0.0.1",
        "::ffff:8.8.8.8",  # any IPv4-mapped address is refused
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "2002:7f00:1::1",  # 6to4 of 127.0.0.1
        "2002:0a00:0001::1",  # 6to4 of 10.0.0.1
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    ],
)
def test_ip_rejected(ip):
    assert not sf.ip_allowed(ip)


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "2002:0808:0808::1"]
)
def test_public_ip_allowed(ip):
    assert sf.ip_allowed(ip)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://100.64.1.1/",
        "http://0.0.0.0/",
        "http://2130706433/",  # decimal-encoded 127.0.0.1 is not a valid public host
        "http://localhost/",
        "http://foo.localhost/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://user:pw@example.com/",
        "http://example.com:22/",
        "http://example.com:6379/",
        "http:///nohost",
    ],
)
async def test_url_rejected(url):
    # decimal host "2130706433" would hit DNS: make it resolve to loopback like getaddrinfo does
    r = resolver_for({"2130706433": ["127.0.0.1"]})
    with pytest.raises(sf.FetchError):
        await sf.vet_url(url, r)


async def test_hostname_resolving_to_private_rejected():
    r = resolver_for({"evil.test": ["10.0.0.7"], "mixed.test": ["8.8.8.8", "127.0.0.1"]})
    for host in ("evil.test", "mixed.test"):
        with pytest.raises(sf.UnsafeURL):
            await sf.vet_url(f"https://{host}/", r)


async def test_public_host_pinned_to_vetted_ip():
    r = resolver_for({"shop.test": ["93.184.216.34"]})
    t = await sf.vet_url("https://shop.test/item?id=1", r)
    assert (t.ip, t.port, t.path) == ("93.184.216.34", 443, "/item?id=1")
    assert sf._pinned_url(t) == "https://93.184.216.34/item?id=1"


def make_transport(handler):
    return httpx.MockTransport(handler)


async def test_connects_to_ip_with_host_and_sni():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["host"], seen["hdr"] = req.url.host, req.headers["host"]
        seen["sni"] = req.extensions.get("sni_hostname")
        seen["cookie"] = req.headers.get("cookie")
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text="<h1>x</h1>"
        )

    r = resolver_for({"shop.test": ["93.184.216.34"]})
    got = await sf.fetch_page("https://shop.test/a", resolver=r, transport=make_transport(handler))
    assert got.text() == "<h1>x</h1>"
    assert seen == {
        "host": "93.184.216.34",
        "hdr": "shop.test",
        "sni": "shop.test",
        "cookie": None,
        "ua": "KesivImporter/1.0",
    }


async def test_redirect_to_private_rejected():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})

    r = resolver_for({"shop.test": ["93.184.216.34"], "internal.test": ["10.0.0.1"]})
    with pytest.raises(sf.UnsafeURL):
        await sf.fetch_page("https://shop.test/", resolver=r, transport=make_transport(handler))


async def test_redirect_to_metadata_ip_literal_rejected():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "http://169.254.169.254/"})

    r = resolver_for({"shop.test": ["93.184.216.34"]})
    with pytest.raises(sf.UnsafeURL):
        await sf.fetch_page("https://shop.test/", resolver=r, transport=make_transport(handler))


async def test_redirects_followed_and_capped():
    def ok(req: httpx.Request) -> httpx.Response:
        if req.headers["host"] == "a.test":
            return httpx.Response(302, headers={"location": "https://b.test/final"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="ok")

    r = resolver_for({"a.test": ["93.184.216.34"], "b.test": ["93.184.216.35"]})
    got = await sf.fetch_page("https://a.test/", resolver=r, transport=make_transport(ok))
    assert got.url == "https://b.test/final"

    def loop(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(sf.FetchError, match="too many redirects"):
        await sf.fetch_page("https://a.test/", resolver=r, transport=make_transport(loop))


async def test_oversize_rejected_declared_and_streamed():
    r = resolver_for({"a.test": ["93.184.216.34"]})

    def declared(req):
        return httpx.Response(
            200, headers={"content-type": "text/html", "content-length": str(sf.PAGE_MAX_BYTES + 1)}
        )

    def streamed(req):
        async def gen():
            for _ in range(5):
                yield b"x" * (1024 * 1024)

        return httpx.Response(200, headers={"content-type": "text/html"}, content=gen())

    for h in (declared, streamed):
        with pytest.raises(sf.FetchError, match="too large"):
            await sf.fetch_page("https://a.test/", resolver=r, transport=make_transport(h))


async def test_content_type_checks():
    r = resolver_for({"a.test": ["93.184.216.34"]})

    def pdf(req):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(sf.FetchError, match="content type"):
        await sf.fetch_page("https://a.test/", resolver=r, transport=make_transport(pdf))

    def html_as_image(req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    with pytest.raises(sf.FetchError, match="content type"):
        await sf.fetch_image(
            "https://a.test/x.png", resolver=r, transport=make_transport(html_as_image)
        )

    def png(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")

    got = await sf.fetch_image("https://a.test/x.png", resolver=r, transport=make_transport(png))
    assert got.body == b"\x89PNG"


async def test_http_error_status():
    r = resolver_for({"a.test": ["93.184.216.34"]})
    with pytest.raises(sf.FetchError, match="HTTP 404"):
        await sf.fetch_page(
            "https://a.test/", resolver=r, transport=make_transport(lambda q: httpx.Response(404))
        )
