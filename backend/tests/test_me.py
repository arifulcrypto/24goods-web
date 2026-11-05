"""PATCH /me, POST /me/credentials, POST /me/delete."""

from __future__ import annotations

from sqlalchemy import text

from tests.conftest import PASSWORD
from tests.helpers import buy, delivered_order


async def test_patch_me(env):
    c, _u = await env.login("buyer")
    r = await c.patch("/api/v1/me", json={"display_name": "  New   Name ", "locale": "zh-CN"})
    assert r.status_code == 200 and r.json()["display_name"] == "New Name"
    assert r.json()["locale"] == "zh-CN"
    assert (await c.patch("/api/v1/me", json={"locale": "fr"})).status_code == 422
    assert (await c.patch("/api/v1/me", json={"display_name": "\x01"})).status_code == 422
    other = env.client()
    assert (await other.patch("/api/v1/me", json={"locale": "en"})).status_code == 401
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='me.profile_updated'") == 1


async def test_change_password_revokes_other_sessions(env):
    c, u = await env.login("buyer")
    other = env.client()
    other.cookies.set("sid", await env.res.sessions.create(u.id, u.tenant_id, u.role))
    assert (await other.get("/api/v1/auth/me")).status_code == 200
    r = await c.post("/api/v1/me/credentials", json={"new_password": "brand-new-password"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invalid_password"
    r = await c.post(
        "/api/v1/me/credentials",
        json={"current_password": "wrong", "new_password": "brand-new-password"},
    )
    assert r.status_code == 403
    r = await c.post(
        "/api/v1/me/credentials",
        json={"current_password": PASSWORD, "new_password": "brand-new-password"},
    )
    assert r.status_code == 200, r.text
    assert (await c.get("/api/v1/auth/me")).status_code == 200  # current session survives
    assert (await other.get("/api/v1/auth/me")).status_code == 401  # others are revoked
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": u.email, "password": "brand-new-password"}
    )
    assert r.status_code == 200
    r = await c.post("/api/v1/me/credentials", json={"current_password": PASSWORD})
    assert r.status_code == 422  # nothing to change


async def test_credentials_email_conflict_and_short_password(env):
    c, _ = await env.login("buyer")
    await env.user("buyer", email="taken@example.com")
    r = await c.post(
        "/api/v1/me/credentials", json={"current_password": PASSWORD, "email": "taken@example.com"}
    )
    assert r.status_code == 409
    r = await c.post(
        "/api/v1/me/credentials", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert r.status_code == 422
    r = await c.post(
        "/api/v1/me/credentials", json={"current_password": PASSWORD, "email": "n@x.invalid"}
    )
    assert r.status_code == 422


async def test_delete_account_redacts_and_blocks_login(env):
    c, u = await env.login("buyer", email="gone@example.com")
    r = await c.post("/api/v1/me/delete", json={"confirm": "NOPE", "password": PASSWORD})
    assert r.status_code == 422
    r = await c.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": "wrong"})
    assert r.status_code == 403
    r = await c.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": PASSWORD})
    assert r.status_code == 204
    assert (
        await env.scalar("SELECT email FROM users WHERE id=:i", i=u.id) == f"deleted+{u.id}@invalid"
    )
    assert await env.scalar("SELECT display_name FROM users WHERE id=:i", i=u.id) == "Deleted user"
    assert await env.scalar("SELECT status FROM users WHERE id=:i", i=u.id) == "deleted"
    assert await env.scalar("SELECT pw_hash FROM users WHERE id=:i", i=u.id) == "!"
    assert (await c.get("/api/v1/auth/me")).status_code == 401
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": "gone@example.com", "password": PASSWORD}
    )
    assert r.status_code == 401
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": f"deleted+{u.id}@invalid", "password": "!"}
    )
    assert r.status_code == 401
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='me.deleted'") == 1


async def test_delete_blocked_by_open_orders_and_tickets(env):
    p = await env.product(mode="manual")
    c, _u = await env.login("buyer")
    order = await buy(c, p)
    r = await c.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_open_orders"
    await c.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    async with env.owner_session() as db:  # cancel it: nothing pending any more
        await db.execute(text("UPDATE orders SET status='cancelled'"))
        await db.commit()
    staff, _ = await env.login("agent")
    c2, _ = await env.login("buyer")
    delivered = await delivered_order(env, c2, p, staff=staff)
    r = await c2.post(
        f"/api/v1/orders/{delivered['id']}/after-sales",
        json={"reason": "other", "description": "help", "attachment_ids": []},
    )
    assert r.status_code == 200, r.text
    r = await c2.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_open_orders"
    r = await c.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": PASSWORD})
    assert r.status_code == 204


async def test_instant_user_can_delete_without_password_staff_cannot(env):
    from tests.helpers import solve_pow

    c = env.client()
    r = await c.post("/api/v1/auth/instant", json={"pow": await solve_pow(c, "instant")})
    assert r.status_code == 200
    assert (await c.post("/api/v1/me/delete", json={"confirm": "DELETE"})).status_code == 204
    staff, _ = await env.login("agent")
    r = await staff.post("/api/v1/me/delete", json={"confirm": "DELETE", "password": PASSWORD})
    assert r.status_code == 403
