"""v5.2 reseller custom domains (Cloudflare for SaaS mocked; the real API is never called)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core import storefront
from app.modules.reseller import service as rsvc
from tests.conftest import PASSWORD
from tests.test_commerce_v4 import set_settings
from tests.test_commerce_v5 import make_reseller

API = "/api/v1"
TOKEN = "cf-secret-token-ABCD1234"
CFID = "cf0123456789abcdef"


class FakeCF:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, str]] = []
        self.active = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            (request.method, request.url.path, body, request.headers["authorization"])
        )
        assert request.url.host == "api.cloudflare.com"
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True, "result": {"id": CFID}})
        st = "active" if self.active else "pending"
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "id": CFID,
                    "status": st,
                    "ownership_verification": {
                        "type": "txt",
                        "name": "_cf-custom-hostname.shop.acme.test",
                        "value": "uuid-1",
                    },
                    "ssl": {
                        "status": "active" if self.active else "pending_validation",
                        "validation_records": [
                            {"txt_name": "_acme-challenge.shop.acme.test", "txt_value": "tok"}
                        ],
                    },
                },
            },
        )


@pytest.fixture
async def cf(env):
    fake = FakeCF()
    old = env.res.http
    env.res.http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield fake
    await env.res.http.aclose()
    env.res.http = old


async def configure(env, admin, *, enabled=True, token=True, zone="zone123") -> None:
    await set_settings(
        env,
        {
            "features": {"resellers": True},
            "resellers": {
                "custom_domains_enabled": enabled,
                "cf_zone_id": zone,
                "fallback_origin": "stores.kesiv.com",
            },
        },
    )
    if token:
        r = await admin.put(
            f"{API}/admin/site/secrets", json={"name": "cloudflare_saas_token", "value": TOKEN}
        )
        assert r.status_code == 200, r.text
        assert TOKEN not in r.text
        assert r.json()["secrets"]["cloudflare_saas_token"]["last4"] == TOKEN[-4:]


async def setup(env):
    admin, _ = await env.login("admin")
    buyer, u = await env.login("buyer")
    await make_reseller(env, u)
    return admin, buyer


async def test_reserved_slugs_fixed_and_setting(env):
    cfg = {"reserved_slugs": ["acme"], "signup_fee_minor": 0}
    assert rsvc.is_reserved_slug("stores", rsvc.section({}, "resellers"))
    assert rsvc.is_reserved_slug("acme", cfg | {"reserved_slugs": ["acme"]})
    assert rsvc.is_reserved_slug("iij", rsvc.section({}, "resellers"))
    assert not rsvc.is_reserved_slug("mystore", rsvc.section({}, "resellers"))
    await set_settings(env, {"features": {"resellers": True}, "resellers": {"signup_fee_minor": 0}})
    c, _ = await env.login("buyer")
    for slug in ("stores", "assets", "pay", "iijweb"):
        r = await c.post(f"{API}/reseller/apply", json={"slug": slug, "store_name": "X"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "slug_reserved", slug


async def test_not_configured_is_409(env, cf):
    admin, buyer = await setup(env)
    await set_settings(env, {"features": {"resellers": True}})
    r = await buyer.get(f"{API}/reseller/domains")
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "custom_domains_unavailable"
    await configure(env, admin, token=False)  # enabled, zone set, but no token
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "custom_domains_unavailable"
    assert cf.calls == []


async def test_add_refresh_resolve_delete(env, cf):
    admin, buyer = await setup(env)
    await configure(env, admin)
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "Shop.ACME.test"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["hostname"] == "shop.acme.test" and d["status"] == "pending"
    assert d["verification"]["ssl_records"][0]["txt_name"].startswith("_acme-challenge")
    assert d["verification"]["ownership"]["value"] == "uuid-1"
    assert TOKEN not in r.text
    m, path, body, auth = cf.calls[0]
    assert (m, path) == ("POST", "/client/v4/zones/zone123/custom_hostnames")
    assert body == {"hostname": "shop.acme.test", "ssl": {"method": "http", "type": "dv"}}
    assert auth == f"Bearer {TOKEN}"
    listing = (await buyer.get(f"{API}/reseller/domains")).json()
    assert listing["fallback_origin"] == "stores.kesiv.com" and len(listing["items"]) == 1
    assert TOKEN not in json.dumps(listing)
    # limit: one domain per reseller
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "other.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domain_limit"
    # not resolving while pending
    web = env.client("shop.acme.test")
    assert (await web.get(f"{API}/site")).json()["storefront"] is None
    cf.active = True
    r = await buyer.post(f"{API}/reseller/domains/{d['id']}/refresh")
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["verified_at"]
    assert cf.calls[-1][:2] == ("GET", f"/client/v4/zones/zone123/custom_hostnames/{CFID}")
    sf = (await web.get(f"{API}/site")).json()["storefront"]
    assert sf and sf["slug"] == "shopa"
    # other hosts unaffected
    assert (await env.client("evil.test").get(f"{API}/site")).json()["storefront"] is None
    r = await buyer.delete(f"{API}/reseller/domains/{d['id']}")
    assert r.status_code == 204
    assert cf.calls[-1][:2] == ("DELETE", f"/client/v4/zones/zone123/custom_hostnames/{CFID}")
    storefront.invalidate()
    assert (await web.get(f"{API}/site")).json()["storefront"] is None
    assert (await buyer.get(f"{API}/reseller/domains")).json()["items"] == []
    async with env.owner_session() as db:
        from sqlalchemy import text

        acts = (
            await db.execute(
                text(
                    "SELECT action, detail::text, after::text FROM audit_log "
                    "WHERE action LIKE 'reseller.domain_%'"
                )
            )
        ).all()
    assert {a[0] for a in acts} == {
        "reseller.domain_added",
        "reseller.domain_refreshed",
        "reseller.domain_removed",
    }
    assert TOKEN not in json.dumps([list(a) for a in acts])


async def test_other_reseller_and_cross_tenant_isolated(env, cf):
    admin, buyer = await setup(env)
    await configure(env, admin)
    d = (await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})).json()
    other, ou = await env.login("buyer")
    await make_reseller(env, ou, slug="shopb")
    assert (await other.get(f"{API}/reseller/domains")).json()["items"] == []
    assert (await other.delete(f"{API}/reseller/domains/{d['id']}")).status_code == 404
    assert (await other.post(f"{API}/reseller/domains/{d['id']}/refresh")).status_code == 404
    # same hostname for another reseller
    r = await other.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domain_taken"
    # RLS: the other tenant's API role sees no row, and cannot resolve the host either
    from sqlalchemy import text

    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT count(*) FROM reseller_domains")) == 0
        assert (
            await storefront._custom_domain_slug(
                db, env.other_tenant_id, "shop.acme.test", "kesiv.com", 0.0
            )
            is None
        )
    # staff of the tenant sees it and can force-remove
    rows = (await admin.get(f"{API}/admin/reseller-domains")).json()
    assert rows[0]["reseller_slug"] == "shopa" and rows[0]["status"] == "pending"
    assert (await buyer.get(f"{API}/admin/reseller-domains")).status_code in (401, 403)
    assert (await admin.delete(f"{API}/admin/reseller-domains/{d['id']}")).status_code == 204
    assert (await buyer.get(f"{API}/reseller/domains")).json()["items"] == []


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.1",
        "nodots",
        "shop.kesiv.com",
        "kesiv.com",
        "stores.kesiv.com",
        "a.stores.kesiv.com",
        "x" * 64 + ".example.com",
        ("a" * 60 + ".") * 5 + "com",
        "bad host.com",
        "-bad.example.com",
    ],
)
async def test_invalid_hostnames_422(env, cf, host):
    admin, buyer = await setup(env)
    await configure(env, admin)
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": host})
    assert r.status_code == 422, (host, r.text)
    assert cf.calls == []


async def test_cf_failure_is_502_and_no_row(env, cf):
    admin, buyer = await setup(env)
    await configure(env, admin)
    env.res.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                400, json={"success": False, "errors": [{"message": "Hostname already exists"}]}
            )
        )
    )
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})
    assert r.status_code == 502 and TOKEN not in r.text
    assert (await buyer.get(f"{API}/reseller/domains")).json()["items"] == []


async def test_recheck_demotes_inactive_and_refresh_404(env, cf):
    from app.modules.reseller import domains

    admin, buyer = await setup(env)
    await configure(env, admin)
    d = (await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})).json()
    cf.active = True
    await buyer.post(f"{API}/reseller/domains/{d['id']}/refresh")
    web = env.client("shop.acme.test")
    assert (await web.get(f"{API}/site")).json()["storefront"]["slug"] == "shopa"
    assert await domains.recheck_active(env.res) == 0  # still active
    cf.active = False  # CF now reports pending / ssl not active
    assert await domains.recheck_active(env.res) == 1
    assert (await web.get(f"{API}/site")).json()["storefront"] is None
    # 404 during manual refresh demotes to failed
    cf.active = True
    await buyer.post(f"{API}/reseller/domains/{d['id']}/refresh")
    env.res.http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(404, json={"success": False, "errors": [{"message": "x"}]})
        )
    )
    r = await buyer.post(f"{API}/reseller/domains/{d['id']}/refresh")
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert (await web.get(f"{API}/site")).json()["storefront"] is None
    # deleting an already-gone hostname still succeeds (CF 404)
    assert (await buyer.delete(f"{API}/reseller/domains/{d['id']}")).status_code == 204


async def test_hostname_owned_elsewhere_refused_before_cf(env, cf):
    from datetime import datetime, timezone

    from app.models import CustomDomain

    admin, buyer = await setup(env)
    await configure(env, admin)
    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            CustomDomain(
                tenant_id=env.other_tenant_id,
                hostname="taken.acme.test",
                verify_token="t",
                status="active",
                verified_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "taken.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domain_taken"
    assert cf.calls == []
    # a reseller_domains row of ANOTHER tenant also blocks, still before CF
    from sqlalchemy import text

    async with env.owner_session(env.other_tenant_id) as db:
        uid = await env.user("buyer", tenant_id=env.other_tenant_id)
        rid = (
            await db.execute(
                text(
                    "INSERT INTO resellers (tenant_id, user_id, slug, store_name, status) "
                    "VALUES (:t, :u, 'otherst', 'O', 'active') RETURNING id"
                ),
                {"t": env.other_tenant_id, "u": uid.id},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO reseller_domains (tenant_id, reseller_id, hostname) "
                "VALUES (:t, :r, 'x.acme.test')"
            ),
            {"t": env.other_tenant_id, "r": rid},
        )
        await db.commit()
    r = await buyer.post(f"{API}/reseller/domains", json={"hostname": "x.acme.test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domain_taken"
    assert cf.calls == []


async def test_failed_insert_after_cf_create_deletes_cf_hostname(env, cf, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    admin, buyer = await setup(env)
    await configure(env, admin)
    real = AsyncSession.commit

    async def boom(self):
        if cf.calls and cf.calls[0][0] == "POST" and len(cf.calls) == 1:
            raise RuntimeError("db down")  # after the CF create, before the domain commit
        return await real(self)

    monkeypatch.setattr(AsyncSession, "commit", boom)
    try:
        await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})
    except RuntimeError:
        pass
    monkeypatch.undo()
    assert [c[0] for c in cf.calls] == ["POST", "DELETE"]


async def test_active_custom_domain_is_not_admin_capable(env, cf):
    admin, buyer = await setup(env)
    await configure(env, admin)
    d = (await buyer.post(f"{API}/reseller/domains", json={"hostname": "shop.acme.test"})).json()
    cf.active = True
    await buyer.post(f"{API}/reseller/domains/{d['id']}/refresh")
    su = await env.user("admin")
    token = await env.res.sessions.create(su.id, su.tenant_id, su.role)
    staff = env.client("shop.acme.test")
    staff.cookies.set("sid", token)
    r = await staff.get(f"{API}/admin/reseller-domains")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_host"
    # staff login on that host is refused as well
    c = env.client("shop.acme.test")
    r = await c.post(f"{API}/auth/login", json={"email": su.email, "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_host"
    # buyers still work there; the main host still serves the admin
    assert (await staff.get(f"{API}/site")).status_code == 200
    assert (await admin.get(f"{API}/admin/reseller-domains")).status_code == 200
