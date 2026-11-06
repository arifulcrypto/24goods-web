"""v5 backend core: archive/categories/settings/secrets/turnstile/email/google/audit/ETA."""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.core import mailer
from app.modules.auth import codes
from tests.conftest import PASSWORD
from tests.helpers import solve_pow

API = "/api/v1"


async def other_tenant_admin(env):
    """Admin of the second tenant, reached through its own host (custom domain)."""
    from app.models import CustomDomain

    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            CustomDomain(
                tenant_id=env.other_tenant_id,
                hostname="other.test",
                verify_token="t",
                status="active",
                verified_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    u = await env.user("admin", tenant_id=env.other_tenant_id)
    token = await env.res.sessions.create(u.id, u.tenant_id, u.role)
    c = env.client("other.test")
    c.cookies.set("sid", token)
    return c, u


async def put_site(env, admin, body):
    r = await admin.put(f"{API}/admin/site", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def set_secret(admin, name, value):
    r = await admin.put(f"{API}/admin/site/secrets", json={"name": name, "value": value})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def outbox(monkeypatch):
    sent: list[dict] = []

    async def fake(cfg, to, subject, body, html=None):
        sent.append({"to": to, "subject": subject, "body": body, "html": html, "cfg": cfg})

    monkeypatch.setattr(mailer, "send_mail", fake)
    return sent


# ------------------------------------------------------------------ archive / delete
async def test_archive_restore_hard_delete_bulk(env):
    admin, _ = await env.login("admin")
    buyer = env.client()
    p = await env.product(mode="manual", title="A")
    unused = await env.product(mode="manual", title="B")
    # archive hides from storefront + default admin list; archived filter shows it
    assert (await admin.delete(f"{API}/admin/products/{p.id}")).status_code == 204
    assert (await buyer.get(f"{API}/products/{p.id}")).status_code == 404
    names = [x["id"] for x in (await admin.get(f"{API}/admin/products")).json()["items"]]
    assert str(p.id) not in names and str(unused.id) in names
    arch = (await admin.get(f"{API}/admin/products", params={"status": "archived"})).json()
    assert [x["id"] for x in arch["items"]] == [str(p.id)]
    r = await admin.post(f"{API}/admin/products/{p.id}/restore")
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert (await admin.post(f"{API}/admin/products/{p.id}/restore")).status_code == 409
    # hard delete: never used -> gone; used by an order -> 409 product_in_use
    assert (await admin.delete(f"{API}/admin/products/{unused.id}?hard=true")).status_code == 204
    assert (await admin.get(f"{API}/admin/products/{unused.id}")).status_code == 404
    await env.scalar("SELECT 1")
    buyer_c, _ = await env.login("buyer")
    await put_site(env, admin, {"payments": {"channels": {"zpay_alipay": {"enabled": True}}}})
    active = await env.product(mode="manual", title="C")
    r = await buyer_c.post(f"{API}/orders", json={"product_id": str(active.id), "qty": 1})
    assert r.status_code == 200, r.text
    r = await admin.delete(f"{API}/admin/products/{active.id}?hard=true")
    assert r.status_code == 409 and r.json()["error"]["code"] == "product_in_use"
    # bulk
    a, b = await env.product(mode="manual", title="D"), await env.product(mode="manual", title="E")
    ids = [str(a.id), str(b.id)]
    r = await admin.post(f"{API}/admin/products/bulk", json={"ids": ids, "action": "archive"})
    assert r.json() == {"updated": 2}
    r = await admin.post(f"{API}/admin/products/bulk", json={"ids": ids, "action": "restore"})
    assert r.json() == {"updated": 2}
    r = await admin.post(
        f"{API}/admin/products/bulk", json={"ids": ids, "action": "set_category", "category": "x"}
    )
    assert r.status_code == 422
    r = await admin.post(
        f"{API}/admin/products/bulk",
        json={"ids": ids, "action": "set_category", "category": "ai_tools"},
    )
    assert r.json() == {"updated": 2}
    acts = await env.scalar(
        "SELECT count(*) FROM audit_log WHERE action IN "
        "('product.archived','product.restored','product.hard_deleted','product.bulk')"
    )
    assert acts >= 6


async def test_cart_quote_unavailable_reason(env):
    admin, _ = await env.login("admin")
    c = env.client()
    live = await env.product(mode="manual", title="Live")
    draft = await env.product(mode="manual", title="Draft", status="draft")
    arch = await env.product(mode="manual", title="Arch")
    await admin.delete(f"{API}/admin/products/{arch.id}")
    r = await c.post(
        f"{API}/cart/quote",
        json={"items": [{"product_id": str(x.id), "qty": 1} for x in (live, draft, arch)]},
    )
    reasons = {ln["title"]: ln["unavailable_reason"] for ln in r.json()["lines"]}
    assert reasons == {"Live": None, "Draft": "unpublished", "Arch": "archived"}


async def test_staff_only_product_fields_and_eta(env):
    admin, _ = await env.login("admin")
    body = {
        "title_i18n": {"en": "Imported", "zh-CN": "导入"},
        "price": "12.00",
        "delivery_mode": "manual",
        "category": "ai_tools",
        "status": "active",
        "delivery_eta_hours": 48,
        "cost_minor": 800,
        "source_url": "https://example.com/p/1",
    }
    r = await admin.post(f"{API}/admin/products", json=body)
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["delivery_eta_hours"], v["cost_minor"], v["source_url"]) == (
        48,
        800,
        "https://example.com/p/1",
    )
    pub = env.client()
    pv = (await pub.get(f"{API}/products/{v['id']}")).json()
    assert pv["delivery_eta_hours"] == 48
    assert "cost_minor" not in pv and "source_url" not in pv
    assert "cost_minor" not in json.dumps((await pub.get(f"{API}/products")).json())
    r = await admin.patch(f"{API}/admin/products/{v['id']}", json={"delivery_eta_hours": None})
    assert r.json()["delivery_eta_hours"] is None
    assert (await pub.get(f"{API}/products/{v['id']}")).json()["delivery_eta_hours"] == 24
    bad = await admin.post(f"{API}/admin/products", json={**body, "source_url": "javascript:x"})
    assert bad.status_code == 422


async def test_order_eta_deadline_and_overdue_filter(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    p = await env.product(mode="manual", title="M")
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "qty": 1})
    o = r.json()
    assert o["delivery_eta_hours"] == 24 and o["delivery_deadline"] is None
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE orders SET status='paid', paid_at = now() - interval '30 hours' "
                "WHERE id = :i"
            ),
            {"i": o["id"]},
        )
        await db.commit()
    got = (await buyer.get(f"{API}/orders/{o['order_no']}")).json()
    assert got["delivery_deadline"] is not None
    over = (await admin.get(f"{API}/admin/orders", params={"overdue_manual": "true"})).json()
    assert [x["id"] for x in over["items"]] == [o["id"]]
    await admin.patch(f"{API}/admin/products/{p.id}", json={"delivery_eta_hours": 72})
    over = (await admin.get(f"{API}/admin/orders", params={"overdue_manual": "true"})).json()
    assert over["items"] == []


# ------------------------------------------------------------------ categories
async def test_categories_crud_hide_validate_and_tenant_isolation(env):
    admin, _ = await env.login("admin")
    other_admin, _ = await other_tenant_admin(env)
    pub = env.client()
    listing = (await pub.get(f"{API}/catalog/categories")).json()
    assert {c["slug"] for c in listing} >= {"gift_cards", "ai_tools"}
    assert all({"slug", "name", "icon"} <= set(c) for c in listing)
    r = await admin.post(
        f"{API}/admin/categories",
        json={"slug": "vpn", "name_i18n": {"en": "VPN", "zh-CN": "加速器"}, "icon": "shield"},
    )
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    dup = await admin.post(
        f"{API}/admin/categories", json={"slug": "vpn", "name_i18n": {"en": "VPN"}}
    )
    assert dup.status_code == 409
    assert (
        await admin.post(
            f"{API}/admin/categories", json={"slug": "Bad Slug", "name_i18n": {"en": "x"}}
        )
    ).status_code == 422
    # another tenant can neither see nor touch it
    assert (await other_admin.get(f"{API}/admin/categories")).json() != []
    assert "vpn" not in {
        c["slug"] for c in (await other_admin.get(f"{API}/admin/categories")).json()
    }
    assert (
        await other_admin.patch(f"{API}/admin/categories/{cid}", json={"enabled": False})
    ).status_code == 404
    assert (await other_admin.delete(f"{API}/admin/categories/{cid}")).status_code == 404
    # product category is validated against the table
    base = {
        "title_i18n": {"en": "P"},
        "price": "1.00",
        "delivery_mode": "manual",
        "status": "active",
    }
    assert (
        await admin.post(f"{API}/admin/products", json={**base, "category": "nope"})
    ).status_code == 422
    pr = await admin.post(f"{API}/admin/products", json={**base, "category": "vpn"})
    assert pr.status_code == 200
    pid = pr.json()["id"]
    # in use -> cannot delete; disabled -> hidden from list/search/recommended, page still loads
    r = await admin.delete(f"{API}/admin/categories/{cid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "category_in_use"
    assert pid in [x["id"] for x in (await pub.get(f"{API}/products")).json()["items"]]
    r = await admin.patch(f"{API}/admin/categories/{cid}", json={"enabled": False})
    assert r.status_code == 200
    assert pid not in [x["id"] for x in (await pub.get(f"{API}/products")).json()["items"]]
    assert pid not in [
        x["id"] for x in (await pub.get(f"{API}/products", params={"q": "P"})).json()["items"]
    ]
    rec = (await pub.get(f"{API}/products/recommended")).json()["items"]
    assert pid not in [x["id"] for x in rec]
    assert (await pub.get(f"{API}/products/{pid}")).status_code == 200
    assert "vpn" not in {c["slug"] for c in (await pub.get(f"{API}/catalog/categories")).json()}
    # unused categories can be deleted
    r2 = await admin.post(f"{API}/admin/categories", json={"slug": "tmp", "name_i18n": {"en": "T"}})
    assert (await admin.delete(f"{API}/admin/categories/{r2.json()['id']}")).status_code == 204


# ------------------------------------------------------------------ settings + secrets
async def test_settings_sections_and_secrets_never_returned(env):
    admin, _ = await env.login("admin")
    other_admin, _ = await other_tenant_admin(env)
    site = await put_site(
        env,
        admin,
        {
            "general": {"site_name_i18n": {"en": "My Shop", "zh-CN": "我的店"}},
            "features": {"registration": False, "resellers": True},
            "defaults": {"max_pending_orders": 3},
            "delivery": {"manual_eta_hours": 12},
            "import": {"markup_value": 250},
            "audit": {"retention_days": 90},
        },
    )
    assert site["general"]["site_name_i18n"]["en"] == "My Shop"
    assert site["features"]["registration"] is False and site["features"]["cart"] is True
    assert site["defaults"]["max_pending_orders"] == 3
    assert "after_sales_default_hours" in site["defaults"]  # legacy keys kept
    assert site["zpay"] == {"configured": True, "pid_last4": "1001"}
    assert site["secrets"]["smtp_password"] == {"configured": False, "last4": None}
    bad = await admin.put(f"{API}/admin/site", json={"features": {"cart": "maybe"}})
    assert bad.status_code == 422
    assert (
        await admin.put(f"{API}/admin/site", json={"audit": {"retention_days": 1}})
    ).status_code == 422
    out = await set_secret(admin, "smtp_password", "hunter2-super-secret")
    assert out["secrets"]["smtp_password"] == {"configured": True, "last4": "cret"}
    blob = json.dumps((await admin.get(f"{API}/admin/site")).json())
    assert "hunter2" not in blob
    pub = json.dumps((await env.client().get(f"{API}/site")).json())
    assert "hunter2" not in pub and "smtp" not in pub.lower().replace("smtp_password", "")
    # ciphertext at rest, bound to tenant (AAD), audit has the name only
    row = await env.scalar("SELECT ciphertext FROM tenant_secrets WHERE name='smtp_password'")
    assert b"hunter2" not in bytes(row)
    from app.core import secretbox

    assert (
        secretbox.unseal(
            env.res.settings.settings_secret(), env.other_tenant_id, "smtp_password", bytes(row)
        )
        is None
    )
    assert (
        secretbox.unseal(
            env.res.settings.settings_secret(), env.tenant_id, "smtp_password", bytes(row)
        )
        == "hunter2-super-secret"
    )
    aud = await env.scalar(
        "SELECT count(*) FROM audit_log WHERE action='settings.secret_set' "
        "AND detail::text NOT LIKE '%hunter2%'"
    )
    assert aud == 1
    assert "hunter2" not in str(
        await env.scalar("SELECT string_agg(detail::text, ',') FROM audit_log")
    )
    # another tenant sees nothing of it
    o = (await other_admin.get(f"{API}/admin/site")).json()
    assert o["secrets"]["smtp_password"]["configured"] is False
    assert o["general"]["site_name_i18n"] == {}
    # delete
    out = await set_secret(admin, "smtp_password", None)
    assert out["secrets"]["smtp_password"]["configured"] is False
    # staff only + validation
    buyer, _ = await env.login("buyer")
    r = await buyer.put(f"{API}/admin/site/secrets", json={"name": "smtp_password", "value": "x"})
    assert r.status_code == 403
    r = await admin.put(f"{API}/admin/site/secrets", json={"name": "evil", "value": "x"})
    assert r.status_code == 422


async def test_public_site_blocks(env):
    admin, _ = await env.login("admin")
    pub = env.client()
    s = (await pub.get(f"{API}/site")).json()
    assert s["features"]["registration"] is True and s["turnstile"]["enabled"] is False
    assert s["google_login"] is False and s["legal"]["enabled"] is False
    assert s["delivery"]["manual_eta_hours"] == 24 and "{hours}" in s["delivery"]["manual_note"]
    await put_site(
        env,
        admin,
        {
            "legal": {"enabled": True, "page_slugs": ["terms", "ghost"]},
            "security": {"turnstile_enabled": True, "turnstile_site_key": "1xKEY"},
        },
    )
    # enabled without a secret -> reported as disabled (no lock-out)
    s = (await pub.get(f"{API}/site")).json()
    assert s["turnstile"] == {"enabled": False, "site_key": "", "actions": []}
    await set_secret(admin, "turnstile_secret", "ts-secret-1234")
    s = (await pub.get(f"{API}/site")).json()
    assert s["turnstile"]["enabled"] and s["turnstile"]["site_key"] == "1xKEY"
    assert "login" in s["turnstile"]["actions"] and "ts-secret" not in json.dumps(s)
    assert s["legal"]["enabled"] and s["legal"]["pages"] == []  # no published page yet


# ------------------------------------------------------------------ feature toggles
async def test_feature_toggles_enforced(env):
    admin, _ = await env.login("admin")
    await put_site(
        env,
        admin,
        {"features": {"registration": False, "guest_checkout": False, "lookup": False}},
    )
    c = env.client()
    r = await c.post(
        f"{API}/auth/register",
        json={"email": "a@b.co", "password": "long-enough-pw", "display_name": "A"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    r = await c.post(f"{API}/auth/instant", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    r = await c.post(f"{API}/lookup", json={"order_no": "GAAAAAAAAAAAA", "access_code": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    await put_site(env, admin, {"features": {"voice_messages": False}})
    buyer, _ = await env.login("buyer")
    r = await buyer.post(
        f"{API}/uploads", data={"kind": "voice"}, files={"file": ("v.webm", b"x", "audio/webm")}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"


async def test_defaults_max_pending_and_timeout(env):
    admin, _ = await env.login("admin")
    await put_site(env, admin, {"defaults": {"max_pending_orders": 1, "order_timeout_minutes": 10}})
    buyer, _ = await env.login("buyer")
    p = await env.product(mode="manual")
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "qty": 1})
    assert r.status_code == 200
    exp = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
    assert timedelta(minutes=8) < exp - datetime.now(timezone.utc) < timedelta(minutes=11)
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "qty": 1})
    assert r.status_code == 409


# ------------------------------------------------------------------ turnstile
class SiteVerify:
    def __init__(self, ok=True, boom=False):
        self.ok, self.boom, self.calls = ok, boom, []

    async def post(self, url, data=None, timeout=None, **kw):
        self.calls.append((url, dict(data or {})))
        if self.boom:
            raise httpx.ConnectTimeout("t")
        return httpx.Response(200, json={"success": self.ok})


async def enable_turnstile(env, admin, secret=True):
    await put_site(
        env,
        admin,
        {"security": {"turnstile_enabled": True, "turnstile_site_key": "1xKEY"}},
    )
    if secret:
        await set_secret(admin, "turnstile_secret", "ts-secret-1234")


async def test_turnstile_pass_fail_reuse_disabled(env, monkeypatch):
    admin, _ = await env.login("admin")
    await env.user("buyer", email="t@example.com")
    c = env.client()
    creds = {"email": "t@example.com", "password": PASSWORD}
    # disabled: no token needed
    assert (await c.post(f"{API}/auth/login", json=creds)).status_code == 200
    # enabled without a secret: skipped (no lock-out)
    await enable_turnstile(env, admin, secret=False)
    assert (await c.post(f"{API}/auth/login", json=creds)).status_code == 200
    await set_secret(admin, "turnstile_secret", "ts-secret-1234")
    sv = SiteVerify(ok=True)
    monkeypatch.setattr(env.res.http, "post", sv.post)
    # missing token -> 400, verify never called
    r = await c.post(f"{API}/auth/login", json=creds)
    assert r.status_code == 400 and r.json()["error"]["code"] == "turnstile_failed"
    assert sv.calls == []
    # pass, with the secret + response forwarded
    r = await c.post(f"{API}/auth/login", json={**creds, "turnstile_token": "tok-1"})
    assert r.status_code == 200
    url, data = sv.calls[0]
    assert url.endswith("/turnstile/v0/siteverify")
    assert data["secret"] == "ts-secret-1234" and data["response"] == "tok-1"
    # single use
    r = await c.post(f"{API}/auth/login", json={**creds, "turnstile_token": "tok-1"})
    assert r.status_code == 400
    # siteverify says no / network failure -> fail closed
    sv.ok = False
    r = await c.post(f"{API}/auth/login", json={**creds, "turnstile_token": "tok-2"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "turnstile_failed"
    sv.ok, sv.boom = True, True
    r = await c.post(f"{API}/auth/login", json={**creds, "turnstile_token": "tok-3"})
    assert r.status_code == 400
    # action not in the list -> not enforced (lookup is off by default), then header token
    sv.boom = False
    r = await c.post(f"{API}/lookup", json={"order_no": "GAAAAAAAAAAAA", "access_code": "x"})
    assert r.status_code != 400 or r.json()["error"]["code"] != "turnstile_failed"
    await put_site(env, admin, {"security": {"turnstile_actions": ["lookup"]}})
    r = await c.post(f"{API}/lookup", json={"order_no": "GAAAAAAAAAAAA", "access_code": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "turnstile_failed"
    r = await c.post(
        f"{API}/lookup",
        json={"order_no": "GAAAAAAAAAAAA", "access_code": "x"},
        headers={"X-Turnstile-Token": "tok-9"},
    )
    assert r.status_code != 400  # passed turnstile (then the usual not-found / pow outcome)
    # staff login honours it too (same endpoint)
    await put_site(env, admin, {"security": {"turnstile_actions": ["login"]}})
    await env.user("admin", email="s@example.com")
    r = await c.post(f"{API}/auth/login", json={"email": "s@example.com", "password": PASSWORD})
    assert r.status_code == 400


# ------------------------------------------------------------------ email code / reset
async def configure_email(env, admin):
    await put_site(
        env,
        admin,
        {
            "email": {
                "enabled": True,
                "host": "smtp.example.com",
                "port": 587,
                "from_addr": "no-reply@example.com",
                "from_name": "Shop",
            }
        },
    )
    await set_secret(admin, "smtp_password", "smtp-pass-0000")


async def test_email_code_register_and_password_reset(env, outbox):
    admin, _ = await env.login("admin")
    c = env.client()
    # email off -> 403
    r = await c.post(
        f"{API}/auth/email-code", json={"email": "n@example.com", "purpose": "register"}
    )
    assert r.status_code == 403
    await configure_email(env, admin)
    await put_site(env, admin, {"security": {"email_verify_on_register": True}})
    reg = {"email": "n@example.com", "password": "long-enough-pw", "display_name": "N"}
    # code required
    body = {**reg, "pow": await solve_pow(c, "register")}
    r = await c.post(f"{API}/auth/register", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    r = await c.post(
        f"{API}/auth/email-code", json={"email": "n@example.com", "purpose": "register"}
    )
    assert r.status_code == 204
    await codes.drain_pending()
    assert len(outbox) == 1 and outbox[0]["to"] == "n@example.com"
    code = next(w for w in outbox[0]["body"].split() if w.strip("*.").isdigit()).strip("*.")
    assert outbox[0]["cfg"].password == "smtp-pass-0000"
    # 1/min per email: second request is silently dropped (still 204)
    r = await c.post(
        f"{API}/auth/email-code", json={"email": "n@example.com", "purpose": "register"}
    )
    assert r.status_code == 204
    await codes.drain_pending()
    assert len(outbox) == 1
    # wrong code then the right one
    bad = await c.post(
        f"{API}/auth/register",
        json={**reg, "email_code": "000000", "pow": await solve_pow(c, "register")},
    )
    assert bad.status_code == 400
    ok = await c.post(
        f"{API}/auth/register",
        json={**reg, "email_code": code, "pow": await solve_pow(c, "register")},
    )
    assert ok.status_code == 200, ok.text
    assert await env.scalar("SELECT email_verified FROM users WHERE email='n@example.com'") is True
    # existing email: 204 but no mail (no enumeration)
    r = await c.post(
        f"{API}/auth/email-code", json={"email": "n@example.com", "purpose": "register"}
    )
    assert r.status_code == 204
    # password reset: unknown email -> 204, no mail; known -> mail
    n = len(outbox)
    r = await c.post(
        f"{API}/auth/email-code", json={"email": "ghost@example.com", "purpose": "reset"}
    )
    assert r.status_code == 204
    await codes.drain_pending()
    assert len(outbox) == n
    s1 = await env.res.sessions.create(
        uuid.UUID(await env.scalar("SELECT id::text FROM users WHERE email='n@example.com'")),
        env.tenant_id,
        "buyer",
    )
    await env.res.redis.flushdb()  # reset the per-email 1/min budget used above
    r = await c.post(f"{API}/auth/email-code", json={"email": "n@example.com", "purpose": "reset"})
    assert r.status_code == 204
    await codes.drain_pending()
    assert len(outbox) == n + 1
    rcode = next(w for w in outbox[-1]["body"].split() if w.strip("*.").isdigit()).strip("*.")
    r = await c.post(
        f"{API}/auth/password-reset",
        json={"email": "n@example.com", "code": "111111", "new_password": "brand-new-password"},
    )
    assert r.status_code == 400
    s1 = await env.res.sessions.create(
        uuid.UUID(await env.scalar("SELECT id::text FROM users WHERE email='n@example.com'")),
        env.tenant_id,
        "buyer",
    )
    r = await c.post(
        f"{API}/auth/password-reset",
        json={"email": "n@example.com", "code": rcode, "new_password": "brand-new-password"},
    )
    assert r.status_code == 204
    assert await env.res.sessions.get(s1) is None  # sessions revoked
    r = await c.post(
        f"{API}/auth/login", json={"email": "n@example.com", "password": "brand-new-password"}
    )
    assert r.status_code == 200
    # the code is single use
    r = await c.post(
        f"{API}/auth/password-reset",
        json={"email": "n@example.com", "code": rcode, "new_password": "another-new-password"},
    )
    assert r.status_code == 400
    # staff cannot reset through email
    await env.user("admin", email="boss@example.com")
    await c.post(f"{API}/auth/email-code", json={"email": "boss@example.com", "purpose": "reset"})
    await codes.drain_pending()
    assert all(m["to"] != "boss@example.com" for m in outbox)


async def test_register_requires_terms_when_legal_enabled(env):
    admin, _ = await env.login("admin")
    await put_site(env, admin, {"legal": {"enabled": True}})
    c = env.client()
    body = {"email": "l@example.com", "password": "long-enough-pw", "display_name": "L"}
    r = await c.post(f"{API}/auth/register", json={**body, "pow": await solve_pow(c, "register")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "terms_required"
    r = await c.post(
        f"{API}/auth/register",
        json={**body, "agree_terms": True, "pow": await solve_pow(c, "register")},
    )
    assert r.status_code == 200


# ------------------------------------------------------------------ templates + order mails
async def test_email_templates_api_and_test_send(env, outbox):
    admin, _ = await env.login("admin")
    site = (await admin.get(f"{API}/admin/site")).json()
    assert set(site["email_templates"]) == {
        "verify_code",
        "password_reset",
        "order_paid",
        "order_delivered",
    }
    assert "{code}" in site["email_templates"]["verify_code"]["body_i18n"]["en"]
    out = await put_site(
        env,
        admin,
        {
            "general": {"site_name_i18n": {"en": "Shopzy"}},
            "email_templates": {
                "order_paid": {
                    "subject_i18n": {"en": "Paid {order_no} @ {site_name} {nope}"},
                    "body_i18n": {
                        "en": "Hi **{title}** cost {amount} <script>x</script> {order_url}"
                    },
                }
            },
        },
    )
    assert out["email_templates"]["order_paid"]["subject_i18n"]["en"].startswith("Paid")
    assert out["email_templates"]["order_paid"]["subject_i18n"]["zh-CN"]  # default kept
    await configure_email(env, admin)
    r = await admin.post(
        f"{API}/admin/site/email/test", json={"to": "me@example.com", "template": "order_paid"}
    )
    assert r.json() == {"ok": True}
    m = outbox[-1]
    assert m["subject"] == "Paid GABCDEFGHIJKL @ Shopzy {nope}"
    assert "<strong>Sample product</strong>" in m["html"] and "<script" not in m["html"]
    # reset a locale to default with an empty string
    out = await put_site(
        env, admin, {"email_templates": {"order_paid": {"subject_i18n": {"en": ""}}}}
    )
    assert "{order_no}" in out["email_templates"]["order_paid"]["subject_i18n"]["en"]
    r = await admin.put(
        f"{API}/admin/site", json={"email_templates": {"order_paid": {"body_i18n": {"fr": "x"}}}}
    )
    assert r.status_code == 422


async def test_order_emails_paid_delivered_only_verified(env, outbox):
    from app.modules import emails

    admin, _ = await env.login("admin")
    await configure_email(env, admin)
    good = await env.user("buyer", email="good@example.com")
    unverified = await env.user("buyer", email="unv@example.com")
    p = await env.product(mode="manual", title="Secret thing")
    now = datetime.now(timezone.utc)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE users SET email_verified = true WHERE id = :i"), {"i": good.id}
        )
        for u in (good, unverified):
            await db.execute(
                text(
                    "INSERT INTO orders (tenant_id, order_no, access_code_hash, buyer_id,"
                    " product_id,"
                    " title, qty, amount_minor, subtotal_minor, fee_minor, fee_permille, currency,"
                    " status, version, delivery_mode, paid_at, delivered_at) VALUES (:t, :no,"
                    " 'h', :u, :p, 'Secret thing', 1, 1990, 1990, 0, 0, 'CNY', 'delivered', 1,"
                    " 'manual', :n, :n)"
                ),
                {
                    "t": env.tenant_id,
                    "no": "G" + uuid.uuid4().hex[:12].upper(),
                    "u": u.id,
                    "p": p.id,
                    "n": now,
                },
            )
        await db.commit()
    assert await emails.enqueue_order_emails(env.res.maker) == 2  # paid + delivered, verified only
    assert await emails.enqueue_order_emails(env.res.maker) == 0  # idempotent
    assert await emails.process_email_outbox(env.res.maker, env.res.settings) == 2
    assert {m["to"] for m in outbox} == {"good@example.com"}
    assert len(outbox) == 2
    assert all("SECRET" not in m["body"].upper().replace("SECRET THING", "") for m in outbox)


# ------------------------------------------------------------------ google
def _id_token(claims: dict) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


async def setup_google(env, admin):
    await put_site(
        env,
        admin,
        {"features": {"google_login": True}, "security": {"google_client_id": "cid.apps.google"}},
    )
    await set_secret(admin, "google_client_secret", "g-secret-9999")


async def test_google_login_flow(env, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    admin, _ = await env.login("admin")
    c = env.client()
    assert (await c.get(f"{API}/auth/oauth/google/start")).status_code == 403
    await setup_google(env, admin)
    assert (await c.get(f"{API}/site")).json()["google_login"] is True
    r = await c.get(f"{API}/auth/oauth/google/start", params={"next": "https://evil.com"})
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["client_id"] == ["cid.apps.google"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://testserver/api/v1/auth/oauth/google/callback"]
    state = q["state"][0]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "cid.apps.google",
        "exp": int(time.time()) + 600,
        "sub": "g-sub-1",
        "email": "Gina@Example.com",
        "email_verified": True,
        "name": "Gina",
    }
    seen = {}

    async def token_post(url, data=None, timeout=None, **kw):
        seen.update(url=url, data=dict(data))
        return httpx.Response(200, json={"id_token": _id_token(claims)})

    monkeypatch.setattr(env.res.http, "post", token_post)
    # state without the browser cookie is rejected (login CSRF)
    c2 = env.client()
    r = await c2.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": state})
    assert r.status_code == 400
    r = await c.get(f"{API}/auth/oauth/google/start", params={"next": "/orders"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = await c.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": state})
    assert r.status_code == 302 and r.headers["location"] == "/orders"
    assert seen["data"]["client_secret"] == "g-secret-9999" and seen["data"]["code_verifier"]
    me = (await c.get(f"{API}/auth/me")).json()
    assert me["email"] == "gina@example.com" and me["role"] == "buyer"
    assert (
        await env.scalar("SELECT email_verified FROM users WHERE email='gina@example.com'") is True
    )
    # state is single use
    assert (
        await c.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": state})
    ).status_code == 400
    # existing verified-email account is linked; wrong audience / unverified refused
    await env.user("buyer", email="link@example.com")
    claims.update(sub="g-sub-2", email="link@example.com")
    c3 = env.client()
    st = parse_qs(
        urlsplit((await c3.get(f"{API}/auth/oauth/google/start")).headers["location"]).query
    )["state"][0]
    assert (
        await c3.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": st})
    ).status_code == 302
    assert (
        await env.scalar("SELECT google_sub FROM users WHERE email='link@example.com'") == "g-sub-2"
    )
    for bad in ({"aud": "other"}, {"email_verified": False}, {"exp": 1}, {"iss": "https://evil"}):
        claims.update({"sub": "g-sub-3", "email": "x@example.com", **bad})
        c4 = env.client()
        st = parse_qs(
            urlsplit((await c4.get(f"{API}/auth/oauth/google/start")).headers["location"]).query
        )["state"][0]
        r = await c4.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": st})
        assert r.status_code == 400, bad
        claims.update(
            aud="cid.apps.google",
            email_verified=True,
            exp=int(time.time()) + 600,
            iss="https://accounts.google.com",
        )
    # staff accounts may not use Google
    await env.user("admin", email="root@example.com")
    claims.update(sub="g-sub-4", email="root@example.com")
    c5 = env.client()
    st = parse_qs(
        urlsplit((await c5.get(f"{API}/auth/oauth/google/start")).headers["location"]).query
    )["state"][0]
    r = await c5.get(f"{API}/auth/oauth/google/callback", params={"code": "c", "state": st})
    assert r.status_code == 403
    assert (await c5.get(f"{API}/auth/me")).status_code == 401


# ------------------------------------------------------------------ audit
async def test_audit_filters_csv_and_purge(env):
    from app.modules import jobs

    admin, a = await env.login("admin")
    await put_site(env, admin, {"features": {"cart": False}})
    p = await env.product(mode="manual")
    await admin.delete(f"{API}/admin/products/{p.id}")
    r = await admin.get(f"{API}/admin/audit", params={"action": "product."})
    items = r.json()["items"]
    assert items and all(i["action"].startswith("product.") for i in items)
    assert {"id", "severity", "before", "after"} <= set(items[0])
    r = await admin.get(f"{API}/admin/audit", params={"actor": a.email[:8], "action": "settings"})
    assert r.json()["total"] >= 1
    assert (await admin.get(f"{API}/admin/audit", params={"severity": "critical"})).json()[
        "total"
    ] == 0
    assert (await admin.get(f"{API}/admin/audit", params={"target": f"product:{p.id}"})).json()[
        "total"
    ] == 1
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert (await admin.get(f"{API}/admin/audit", params={"since": future})).json()["total"] == 0
    assert (await admin.get(f"{API}/admin/audit", params={"severity": "bogus"})).status_code == 422
    r = await admin.get(f"{API}/admin/audit.csv", params={"action": "product."})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,at,actor") and len(lines) >= 2
    agent, _ = await env.login("agent")
    assert (await agent.get(f"{API}/admin/audit.csv")).status_code == 403
    # retention purge honours audit.retention_days (min 30)
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE audit_log SET created_at = now() - interval '400 days' "
                "WHERE action='product.archived'"
            )
        )
        await db.commit()
    assert await jobs.purge_audit(env.res.maker) == 1
    assert (await admin.get(f"{API}/admin/audit", params={"action": "product.archived"})).json()[
        "total"
    ] == 0
    assert (await admin.get(f"{API}/admin/audit", params={"action": "settings"})).json()[
        "total"
    ] >= 1
