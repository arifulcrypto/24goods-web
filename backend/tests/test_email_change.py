"""S6 (#72): changing the login email needs a code mailed to the NEW address."""

from __future__ import annotations

import pytest

from app.core import mailer
from app.modules.auth import codes
from tests.test_v5_core import API, configure_email, other_tenant_admin

NEW = "fresh@example.com"


@pytest.fixture
def outbox(monkeypatch):
    sent: list[dict] = []

    async def fake(cfg, to, subject, body, html=None):
        sent.append({"to": to, "body": body})

    monkeypatch.setattr(mailer, "send_mail", fake)
    return sent


def _code(outbox, to):
    mail = next(m for m in reversed(outbox) if m["to"] == to)
    return next(w for w in mail["body"].split() if w.strip("*.").isdigit()).strip("*.")


async def _req(c, email, pw="x"):
    return await c.post(f"{API}/me/email-change", json={"email": email, "current_password": pw})


async def _setup(env):
    admin, _ = await env.login("admin")
    await configure_email(env, admin)
    from tests.conftest import PASSWORD

    buyer, user = await env.login("buyer")
    return buyer, user, PASSWORD


async def test_unconfirmed_email_never_stored_and_confirm_works(env, outbox):
    buyer, user, pw = await _setup(env)
    old = await env.scalar("SELECT email FROM users WHERE id=:i", i=user.id)
    # the old direct path no longer changes the email
    r = await buyer.post(f"{API}/me/credentials", json={"email": NEW, "current_password": pw})
    assert r.status_code == 422
    r = await buyer.post(
        f"{API}/me/credentials",
        json={"email": NEW, "new_password": "brand-new-password-1", "current_password": pw},
    )
    assert r.status_code == 200
    assert await env.scalar("SELECT email FROM users WHERE id=:i", i=user.id) == old
    # wrong current password: nothing sent
    r = await _req(buyer, NEW, "wrong-password")
    assert r.status_code == 403 and not outbox
    r = await _req(buyer, NEW, "brand-new-password-1")
    assert r.status_code == 200 and r.json() == {"sent": True}
    await codes.drain_pending()
    assert await env.scalar("SELECT email FROM users WHERE id=:i", i=user.id) == old  # still old
    code = _code(outbox, NEW)
    r = await buyer.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": "000000"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_code"
    r = await buyer.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": code})
    assert r.status_code == 200 and r.json()["email"] == NEW
    assert await env.scalar("SELECT email_verified FROM users WHERE id=:i", i=user.id) is True
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='me.email_changed'") == 1
    # reused code
    r = await buyer.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": code})
    assert r.status_code == 400


async def test_request_identical_for_free_and_taken_address(env, outbox):
    buyer, _, pw = await _setup(env)
    _other, other_user = await env.login("buyer")
    taken = await env.scalar("SELECT email FROM users WHERE id=:i", i=other_user.id)
    r1 = await _req(buyer, NEW, pw)
    r2 = await _req(buyer, taken, pw)
    await codes.drain_pending()
    assert (r1.status_code, r1.json()) == (r2.status_code, r2.json()) == (200, {"sent": True})
    assert [m["to"] for m in outbox] == [NEW]  # nothing sent to the taken address


async def test_code_bound_to_user_and_address(env, outbox):
    buyer, _, pw = await _setup(env)
    r = await _req(buyer, NEW, pw)
    assert r.status_code == 200
    await codes.drain_pending()
    code = _code(outbox, NEW)
    # another user (same tenant) cannot use it
    seller, _ = await env.login("buyer")
    r = await seller.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": code})
    assert r.status_code == 400
    # and it does not work for a different address
    r = await buyer.post(
        f"{API}/me/email-change/confirm", json={"email": "other@example.com", "code": code}
    )
    assert r.status_code == 400
    # a user of another tenant cannot use it either
    oadmin, _ = await other_tenant_admin(env)
    r = await oadmin.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": code})
    assert r.status_code == 400
    # the rightful owner still can
    r = await buyer.post(f"{API}/me/email-change/confirm", json={"email": NEW, "code": code})
    assert r.status_code == 200


async def test_email_change_needs_mailer(env):
    buyer, _ = await env.login("buyer")
    r = await _req(buyer, NEW)
    assert r.status_code == 403
