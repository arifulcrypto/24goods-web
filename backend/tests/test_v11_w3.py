"""review-v11 W3: announcements, manual service records, pin & sort, avatar presets."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.errors import NotFound
from app.models import Category
from app.models.operations import ServiceEntitlement, ServiceReminder, ServiceRenewal
from app.modules.operations import jobs
from app.modules.operations import service as ops_service

API = "/api/v1"


# ------------------------------------------------------------------ announcements
async def publish(admin, title="Maintenance", status="published", **extra):
    r = await admin.post(
        f"{API}/admin/announcements",
        json={"title_i18n": {"en": title, "zh-CN": f"{title}-zh"}, "body_i18n": {"en": "**b**"},
              "status": status, **extra},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


async def test_announcement_publish_unread_read(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    a1 = await publish(admin, "One")
    a2 = await publish(admin, "Two", category="operations")
    draft = await publish(admin, "Draft", status="draft")
    assert draft["published_at"] is None

    lst = (await buyer.get(f"{API}/announcements")).json()
    assert lst["unread"] == 2 and lst["total"] == 2  # drafts are invisible
    assert {i["title"] for i in lst["items"]} == {"One", "Two"}
    assert all(i["read"] is False for i in lst["items"])
    assert (await buyer.get(f"{API}/announcements/{draft['id']}")).status_code == 404

    site = (await buyer.get(f"{API}/site")).json()["announcements"]
    assert site["unread"] == 2 and site["prompt_new"] is True
    assert site["newest"]["id"] == a2["id"]

    detail = (await buyer.get(f"{API}/announcements/{a1['id']}")).json()
    assert "<strong>b</strong>" in detail["body_html"]

    assert (await buyer.post(f"{API}/announcements/{a2['id']}/read")).status_code == 204
    assert (
        await buyer.post(f"{API}/announcements/{a2['id']}/read")
    ).status_code == 204  # idempotent
    site = (await buyer.get(f"{API}/site")).json()["announcements"]
    assert site["unread"] == 1 and site["newest"]["id"] == a1["id"]
    assert (await buyer.post(f"{API}/announcements/{draft['id']}/read")).status_code == 404

    assert (await buyer.post(f"{API}/announcements/read-all")).status_code == 204
    assert (await buyer.get(f"{API}/announcements")).json()["unread"] == 0

    # guests: list works, no read state
    guest = env.client()
    g = (await guest.get(f"{API}/announcements")).json()
    assert g["unread"] is None and g["items"][0]["read"] is None
    assert (await guest.post(f"{API}/announcements/read-all")).status_code == 401
    gsite = (await guest.get(f"{API}/site")).json()["announcements"]
    assert gsite["unread"] is None and gsite["newest"]["id"] == a2["id"]

    # admin sees the read count
    rows = (await admin.get(f"{API}/admin/announcements")).json()["items"]
    assert {r["id"]: r["read_count"] for r in rows}[a2["id"]] == 1
    # announcements are not articles
    assert (await env.client().get(f"{API}/articles")).json()["total"] == 0


async def test_announcement_edit_publish_delete_audited_and_rbac(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    for c in (buyer, agent):
        assert (
            await c.post(f"{API}/admin/announcements", json={"title_i18n": {"en": "x"}})
        ).status_code == 403
    d = await publish(admin, "Soon", status="draft")
    r = await admin.patch(
        f"{API}/admin/announcements/{d['id']}", json={"status": "published", "pinned": True}
    )
    assert r.status_code == 200 and r.json()["published_at"] and r.json()["pinned"]
    assert (await buyer.get(f"{API}/announcements")).json()["total"] == 1
    # staff also use the buyer-style endpoints for their own unread state
    assert (await agent.get(f"{API}/announcements")).json()["unread"] == 1
    r = await admin.patch(
        f"{API}/admin/announcements/{d['id']}", json={"title_i18n": {"en": "Later"}}
    )
    assert r.json()["title_i18n"]["en"] == "Later"
    # settings
    assert (await admin.get(f"{API}/admin/announcements/settings")).json() == {
        "prompt_new": True,
        "show_badge": True,
    }
    r = await admin.put(f"{API}/admin/announcements/settings", json={"prompt_new": False})
    assert r.json()["prompt_new"] is False
    assert (await buyer.get(f"{API}/site")).json()["announcements"]["prompt_new"] is False
    assert (await admin.delete(f"{API}/admin/announcements/{d['id']}")).status_code == 204
    assert (await buyer.get(f"{API}/announcements")).json()["total"] == 0
    actions = set(await env.res_audit_actions()) if hasattr(env, "res_audit_actions") else None
    del actions
    async with env.owner_session() as db:
        from sqlalchemy import text

        got = set(
            await db.scalars(
                text("SELECT action FROM audit_log WHERE action LIKE 'announcement.%'")
            )
        )
    assert {
        "announcement.published",
        "announcement.updated",
        "announcement.settings",
        "announcement.deleted",
    } <= got


async def test_announcement_cross_tenant(env):
    from app.models import AnnouncementRead, Article

    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    a = await publish(admin, "Mine")
    await buyer.post(f"{API}/announcements/{a['id']}/read")
    path = f"{API}/admin/announcements/{a['id']}"
    # a session of another tenant is not accepted on this tenant's host at all
    assert (await other_admin.patch(path, json={"pinned": True})).status_code in (401, 403, 404)
    assert (await other_admin.delete(path)).status_code in (401, 403, 404)
    # and the rows are invisible (query filter + RLS) from the other tenant's session
    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(select(func.count()).select_from(Article)) == 0
        assert await db.scalar(select(func.count()).select_from(AnnouncementRead)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(Article).where(Article.id == uuid.UUID(a["id"]))
            )
            == 0
        )
    assert (await admin.get(f"{API}/admin/announcements")).json()["total"] == 1


# ------------------------------------------------------------------ manual service records
START = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0)


def record(**over):
    body = {
        "label": "Netflix 4K", "customer_label": "Alice (tg @alice)", "contact": "tg:@alice",
        "notes": "pays monthly", "starts_at": START.isoformat(),
        "ends_at": (START + timedelta(days=30)).isoformat(), "email_reminders": True,
        "reminder_lead_days": 5,
    }  # fmt: skip
    body.update(over)
    return body


async def test_manual_record_without_buyer_or_order(env):
    agent, _ = await env.login("agent")
    r = await agent.post(f"{API}/admin/services", json=record())
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["ref"].startswith("SV-") and len(row["ref"]) == 9
    assert row["buyer_id"] is None and row["order_id"] is None and row["standalone"] is True
    assert row["status"] == "active" and row["contact"] == "tg:@alice"

    # reminders: lead day configured (before_5) + expiry
    res = env.res
    async with env.owner_session() as db:
        ent = await db.scalar(
            select(ServiceEntitlement).where(ServiceEntitlement.ref == row["ref"])
        )
        assert ent.reminder_lead_days == 5
        kinds = {k for k, _ in jobs.schedule(ent, START)}
    assert kinds == {"before_5:email", "expiry:email"}
    assert await jobs.schedule_reminders(res) >= 2
    async with env.owner_session() as db:
        n = await db.scalar(
            select(ServiceReminder.id).where(ServiceReminder.entitlement_id == ent.id).limit(1)
        )
    assert n is not None

    # search by ref / customer / contact / label
    for term in (row["ref"], row["ref"].lower(), "alice", "@alice", "netflix"):
        found = (await agent.get(f"{API}/admin/services", params={"q": term})).json()
        assert found["total"] == 1, term
    assert (await agent.get(f"{API}/admin/services", params={"q": "nomatch"})).json()["total"] == 0
    detail = (await agent.get(f"{API}/admin/services/{row['id']}")).json()
    assert detail["orders"] == [] and detail["manual_renewals"] == []

    # manual edit (no order needed)
    r = await agent.patch(
        f"{API}/admin/services/{row['id']}",
        json={"reason": "typo", "customer_label": "Alice B", "notes": "n2"},
    )
    assert r.status_code == 200, r.text

    # manual renewal without an order, extends from the current end
    r = await agent.post(
        f"{API}/admin/services/{row['id']}/renewals",
        json={"reason": "paid in cash"},
    )
    assert r.status_code == 200, r.text
    expected = ops_service.period_end(START + timedelta(days=30), "calendar_month", 30, "UTC")
    assert datetime.fromisoformat(r.json()["ends_at"]) == expected
    detail = (await agent.get(f"{API}/admin/services/{row['id']}")).json()
    assert (
        len(detail["manual_renewals"]) == 1
        and detail["manual_renewals"][0]["note"] == "paid in cash"
    )
    async with env.owner_session() as db:
        ren = list(await db.scalars(select(ServiceRenewal)))
        assert len(ren) == 1 and ren[0].order_id is None
    # refs are unique per tenant and audited
    r2 = await agent.post(f"{API}/admin/services", json=record(label="Spotify"))
    assert r2.json()["ref"] != row["ref"]
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action IN ('service.created','service.renewed')"
        )
        == 3
    )


async def test_manual_record_validation_links_and_isolation(env):
    agent, _ = await env.login("agent")
    other, _ = await env.login("agent", tenant_id=env.other_tenant_id)
    _buyer, bu = await env.login("buyer", email="link@example.com")
    r = await agent.post(f"{API}/admin/services", json={"customer_label": "x"})
    assert r.status_code == 422  # a label (or a paid order) is required
    r = await agent.post(
        f"{API}/admin/services", json=record(ends_at=(START - timedelta(days=1)).isoformat())
    )
    assert r.status_code == 422  # end before start
    r = await agent.post(f"{API}/admin/services", json=record(buyer_email="Link@Example.com"))
    assert r.status_code == 201 and r.json()["buyer_id"] == str(bu.id)
    r = await agent.post(f"{API}/admin/services", json=record(buyer_email="nobody@example.com"))
    assert r.status_code == 201 and r.json()["buyer_id"] is None
    rid = r.json()["id"]
    assert (
        await agent.post(f"{API}/admin/services", json=record(order_no="NOPE"))
    ).status_code == 404
    # no period and no end: derived from the policy
    r = await agent.post(
        f"{API}/admin/services",
        json=record(ends_at=None, period_policy="fixed_days", period_days=10),
    )
    assert datetime.fromisoformat(r.json()["ends_at"]) == START + timedelta(days=10)
    # cross-tenant
    assert (await other.get(f"{API}/admin/services/{rid}")).status_code in (401, 403, 404)
    async with env.api_session(env.other_tenant_id) as db:
        with pytest.raises(NotFound):
            await ops_service.owned(db, env.other_tenant_id, uuid.UUID(rid))
        assert await db.scalar(select(func.count()).select_from(ServiceEntitlement)) == 0


# ------------------------------------------------------------------ pin & sort
async def listing(client, **params):
    r = await client.get(f"{API}/products", params=params)
    assert r.status_code == 200, r.text
    return [i["title"] for i in r.json()["items"]]


async def test_pin_and_sort_every_sort_pinned_first(env):
    admin, _ = await env.login("admin")
    a = await env.product(title="A", price=300, floor=0)
    b = await env.product(title="B", price=100, floor=0)
    c = await env.product(title="C", price=200, floor=0)
    async with env.owner_session() as db:
        from sqlalchemy import text

        await db.execute(text("UPDATE products SET sold = 5 WHERE title_i18n->>'en' = 'B'"))
        await db.execute(text("UPDATE products SET sold = 9 WHERE title_i18n->>'en' = 'C'"))
        await db.commit()
    shop = env.client()
    assert await listing(shop, sort="sales") == ["C", "B", "A"]
    assert await listing(shop, sort="price_asc") == ["B", "C", "A"]
    assert await listing(shop, sort="price_desc") == ["A", "C", "B"]
    assert (await shop.get(f"{API}/products", params={"sort": "bogus"})).status_code == 422

    r = await admin.put(f"{API}/admin/products/{a.id}/pin", json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"] is True
    for sort in ("manual", "sales", "price_asc", "price_desc", "newest"):
        assert (await listing(shop, sort=sort))[0] == "A", sort
    # newest pin first
    await admin.put(f"{API}/admin/products/{b.id}/pin", json={"pinned": True})
    assert (await listing(shop))[:2] == ["B", "A"]
    await admin.put(f"{API}/admin/products/{b.id}/pin", json={"pinned": False})
    await admin.put(f"{API}/admin/products/{a.id}/pin", json={"pinned": False})
    # default sort setting drives the unspecified listing
    assert (await admin.get(f"{API}/admin/catalog/settings")).json()["default_sort"] == "manual"
    r = await admin.put(f"{API}/admin/catalog/settings", json={"default_sort": "price_asc"})
    assert r.status_code == 200
    assert await listing(shop) == ["B", "C", "A"]
    assert (await shop.get(f"{API}/site")).json()["catalog"]["default_sort"] == "price_asc"
    assert (
        await admin.put(f"{API}/admin/catalog/settings", json={"default_sort": "x"})
    ).status_code == 422
    # admin product view carries the flag
    await admin.put(f"{API}/admin/products/{c.id}/pin", json={"pinned": True})
    adm = (await admin.get(f"{API}/admin/products")).json()["items"]
    assert adm[0]["title_i18n"]["en"] == "C" and adm[0]["pinned"] is True


async def test_bulk_reorder_audited_and_cross_tenant(env):
    admin, _ = await env.login("admin")
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    ps = [await env.product(title=t, floor=0) for t in ("P1", "P2", "P3")]
    foreign = await env.product(title="F", floor=0, tenant_id=env.other_tenant_id)
    ids = [str(ps[2].id), str(ps[0].id), str(ps[1].id)]
    r = await admin.post(f"{API}/admin/products/reorder", json={"ids": ids})
    assert r.status_code == 200 and r.json()["count"] == 3
    shop = env.client()
    assert await listing(shop) == ["P3", "P1", "P2"]
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='product.reordered'") == 1
    # duplicates / foreign ids refused and nothing changes
    assert (
        await admin.post(f"{API}/admin/products/reorder", json={"ids": [ids[0], ids[0]]})
    ).status_code == 422
    assert (
        await admin.post(f"{API}/admin/products/reorder", json={"ids": [ids[0], str(foreign.id)]})
    ).status_code == 404
    assert (
        await other_admin.put(f"{API}/admin/products/{ps[0].id}/pin", json={"pinned": True})
    ).status_code in (401, 403, 404)
    assert await listing(shop) == ["P3", "P1", "P2"]

    # categories: pin + reorder
    cats = (await admin.get(f"{API}/admin/categories")).json()
    slugs = [c["slug"] for c in cats]
    by = {c["slug"]: c["id"] for c in cats}
    last = slugs[-1]
    r = await admin.put(f"{API}/admin/categories/{by[last]}/pin", json={"pinned": True})
    assert r.status_code == 200
    pub = (await env.client().get(f"{API}/categories")).json()
    assert pub[0]["slug"] == last
    await admin.put(f"{API}/admin/categories/{by[last]}/pin", json={"pinned": False})
    order = list(reversed([by[s] for s in slugs]))
    assert (
        await admin.post(f"{API}/admin/categories/reorder", json={"ids": order})
    ).status_code == 200
    pub = (await env.client().get(f"{API}/categories")).json()
    assert [c["slug"] for c in pub] == list(reversed(slugs))
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action IN "
            "('category.reordered','category.pinned','category.unpinned')"
        )
        == 3
    )
    assert (
        await other_admin.post(f"{API}/admin/categories/reorder", json={"ids": [by[last]]})
    ).status_code in (401, 403, 404)
    # a foreign category id on this tenant's session is a 404
    async with env.api_session(env.other_tenant_id) as db:
        foreign_cat = await db.scalar(select(Category.id).limit(1))
    r = await admin.post(f"{API}/admin/categories/reorder", json={"ids": [str(foreign_cat)]})
    assert r.status_code == 404


# ------------------------------------------------------------------ avatars
async def test_avatar_preset(env):
    buyer, u = await env.login("buyer")
    r = await buyer.put(f"{API}/me/avatar/preset", json={"preset": "p07"})
    assert (
        r.status_code == 200
        and r.json()["avatar_preset"] == "p07"
        and r.json()["avatar_url"] is None
    )
    assert (await buyer.get(f"{API}/auth/me")).json()["avatar_preset"] == "p07"
    for bad in ("p17", "p00", "x", "../p01", ""):
        assert (await buyer.put(f"{API}/me/avatar/preset", json={"preset": bad})).status_code == 422
    assert (
        await env.client().put(f"{API}/me/avatar/preset", json={"preset": "p01"})
    ).status_code == 401

    # an upload replaces the preset, a preset clears an upload
    async with env.owner_session() as db:
        from sqlalchemy import text

        await db.execute(
            text("UPDATE users SET avatar_key='abcdef0123456789abcdef0123456789' WHERE id=:i"),
            {"i": u.id},
        )
        await db.commit()
    me = (await buyer.get(f"{API}/auth/me")).json()
    assert me["avatar_url"] and me["avatar_preset"] is None  # upload wins in views
    r = await buyer.put(f"{API}/me/avatar/preset", json={"preset": "p03"})
    assert r.json()["avatar_url"] is None and r.json()["avatar_preset"] == "p03"
    assert await env.scalar("SELECT avatar_key FROM users WHERE id=:i", i=u.id) is None
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='me.avatar_preset'") == 2

    # shows up in chat sender and inbox views
    from tests.helpers import open_conversation

    agent, _au = await env.login("agent")
    assert (await agent.put(f"{API}/me/avatar/preset", json={"preset": "p12"})).status_code == 200
    conv = await open_conversation(buyer)
    await buyer.post(
        f"{API}/conversations/{conv['id']}/messages",
        json={"client_msg_id": "m1", "type": "text", "text": "hi"},
    )
    await agent.post(
        f"{API}/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "m2", "type": "text", "text": "yo"},
    )
    msgs = (await buyer.get(f"{API}/conversations/{conv['id']}/messages")).json()
    senders = {
        m["sender"]["role"]: m["sender"] for m in msgs if m["sender"]["role"] in ("buyer", "agent")
    }
    assert senders["buyer"]["avatar_preset"] == "p03" and senders["agent"]["avatar_preset"] == "p12"
    inbox = (await agent.get(f"{API}/admin/conversations")).json()
    items = inbox["items"] if isinstance(inbox, dict) else inbox
    row = next(i for i in items if i["id"] == conv["id"])
    assert row["buyer"]["avatar_preset"] == "p03"
    # removing the avatar clears the preset too
    assert (await buyer.delete(f"{API}/me/avatar")).json()["avatar_preset"] is None
