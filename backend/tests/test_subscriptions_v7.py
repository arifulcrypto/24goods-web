"""Subscription products, month-based after-sales, auto services and the admin read models."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.models.operations import ServiceEntitlement, ServiceRenewal
from app.modules.common.sitedefaults import (
    add_months,
    after_sales_default_hours,
    delivery_deadlines,
    resolve_after_sales_hours,
    resolve_after_sales_months,
)
from app.modules.operations import jobs, service
from tests.helpers import delivered_order

API = "/api/v1"
UTC = timezone.utc


def dt(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


# ------------------------------------------------------------------ pure arithmetic


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (dt(2025, 1, 31, 10), 1, dt(2025, 2, 28, 10)),
        (dt(2024, 1, 31, 10), 1, dt(2024, 2, 29, 10)),
        (dt(2026, 12, 31, 10), 1, dt(2027, 1, 31, 10)),
        (dt(2026, 11, 15, 10), 3, dt(2027, 2, 15, 10)),
        (dt(2025, 1, 31, 10), 2, dt(2025, 3, 31, 10)),
        (dt(2026, 6, 30, 23, 59), 12, dt(2027, 6, 30, 23, 59)),
    ],
)
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


def test_periods_end_counts_from_start_not_by_repeated_clamping():
    start = dt(2025, 1, 31, 10)
    assert service.periods_end(start, "calendar_month", 30, "UTC", 2) == dt(2025, 3, 31, 10)
    assert service.periods_end(start, "calendar_month", 30, "UTC", 1) == service.period_end(
        start, "calendar_month", 30, "UTC"
    )
    assert service.periods_end(start, "fixed_days", 10, "UTC", 3) == start + timedelta(days=30)


def test_after_sales_resolution_order_and_legacy_setting():
    # product hours -> product months -> tenant default (new key) -> legacy hours -> 1 month
    assert resolve_after_sales_hours(24, {}, 3) == 24
    assert resolve_after_sales_months(24, 3, {}) is None
    assert resolve_after_sales_hours(0, {}, 3) == 0
    assert resolve_after_sales_months(None, 3, {}) == 3
    assert resolve_after_sales_hours(None, {}, 3) == 3 * 720
    assert resolve_after_sales_months(None, None, None) == 1
    assert after_sales_default_hours(None) == 720
    assert resolve_after_sales_months(None, None, {"after_sales_default_hours": 100}) is None
    assert resolve_after_sales_hours(None, {"after_sales_default_hours": 100}) == 100
    both = {"after_sales_default_hours": 100, "after_sales_default": {"unit": "month", "value": 2}}
    assert resolve_after_sales_months(None, None, both) == 2  # the new key wins
    hours = {"after_sales_default": {"unit": "hour", "value": 48}}
    assert resolve_after_sales_hours(None, hours) == 48
    bad = {"after_sales_default": {"unit": "month", "value": 99}}
    assert resolve_after_sales_months(None, None, bad) == 1  # invalid falls back


def test_delivery_deadlines_use_calendar_months():
    now = dt(2025, 1, 31, 8)
    until, confirm = delivery_deadlines(now, mode="manual", product_hours=None, tenant_settings={})
    assert until == dt(2025, 2, 28, 8) and confirm == now + timedelta(hours=72)
    until, _ = delivery_deadlines(
        now, mode="manual", product_hours=None, tenant_settings={}, product_months=12
    )
    assert until == dt(2026, 1, 31, 8)
    until, _ = delivery_deadlines(now, mode="manual", product_hours=0, tenant_settings={})
    assert until is None


# ------------------------------------------------------------------ products + settings API


async def test_product_after_sales_units_clear_each_other_and_flow_to_views(env):
    admin, _ = await env.login("admin")
    pub = env.client()
    p = await env.product(mode="manual")
    url = f"{API}/admin/products/{p.id}"
    r = await admin.patch(url, json={"after_sales_months": 3})
    assert r.status_code == 200, r.text
    assert r.json()["after_sales_months"] == 3 and r.json()["after_sales_hours"] is None
    d = (await pub.get(f"{API}/products/{p.id}")).json()
    assert d["after_sales_months"] == 3 and d["after_sales_hours"] == 3 * 720
    r = await admin.patch(url, json={"after_sales_hours": 48})
    assert r.json()["after_sales_months"] is None and r.json()["after_sales_hours"] == 48
    d = (await pub.get(f"{API}/products/{p.id}")).json()
    assert d["after_sales_months"] is None and d["after_sales_hours"] == 48
    r = await admin.patch(url, json={"after_sales_hours": 1, "after_sales_months": 1})
    assert r.status_code == 422 or r.status_code == 400
    r = await admin.patch(url, json={"after_sales_hours": None, "after_sales_months": None})
    d = (await pub.get(f"{API}/products/{p.id}")).json()
    assert d["after_sales_months"] == 1  # tenant default: one calendar month


async def test_product_subscription_switch_and_settings_default(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual")
    url = f"{API}/admin/products/{p.id}"
    assert (await admin.get(url)).json()["subscription"] is None
    r = await admin.patch(url, json={"subscription": {"grace_days": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["subscription"] == {
        "period_policy": "calendar_month",
        "period_days": 30,
        "grace_days": 2,
        "email_reminders": True,
        "telegram_reminders": True,
    }
    r = await admin.patch(url, json={"subscription": {"grace_days": 99}})
    assert r.status_code in (400, 422)
    r = await admin.patch(url, json={"subscription": None})
    assert r.json()["subscription"] is None
    assert await env.scalar("SELECT subscription IS NULL FROM products WHERE id=:i", i=p.id)
    # tenant default: new key, legacy field still accepted
    site = (await admin.get(f"{API}/admin/site")).json()
    assert site["after_sales_default"] == {"unit": "month", "value": 1}
    r = await admin.put(
        f"{API}/admin/site", json={"after_sales_default": {"unit": "month", "value": 2}}
    )
    assert r.status_code == 200, r.text
    assert (await env.client().get(f"{API}/site")).json()["after_sales_default"]["value"] == 2
    r = await admin.put(
        f"{API}/admin/site", json={"after_sales_default": {"unit": "month", "value": 13}}
    )
    assert r.status_code in (400, 422)
    r = await admin.put(f"{API}/admin/site", json={"after_sales_default_hours": 100})
    assert r.status_code == 200
    site = (await admin.get(f"{API}/admin/site")).json()
    assert site["after_sales_default"] == {"unit": "hour", "value": 100}
    assert site["after_sales_default_hours"] == 100


async def test_manual_delivery_uses_product_months(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await env.product(mode="manual")
    await admin.patch(f"{API}/admin/products/{p.id}", json={"after_sales_months": 2})
    o = await delivered_order(env, buyer, p, staff=staff)
    d = datetime.fromisoformat(o["delivered_at"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(o["after_sales_until"].replace("Z", "+00:00"))
    assert until == add_months(d, 2)


# ------------------------------------------------------------------ sync job


async def subscription_product(env, admin, **sub):
    p = await env.product(mode="manual", title="Netflix Premium", price=1000, floor=100)
    r = await admin.patch(f"{API}/admin/products/{p.id}", json={"subscription": sub})
    assert r.status_code == 200, r.text
    return p


async def entitlements(env, tenant=None):
    async with env.api_session(tenant) as db:
        return list(
            await db.scalars(
                select(ServiceEntitlement)
                .where(ServiceEntitlement.tenant_id == (tenant or env.tenant_id))
                .order_by(ServiceEntitlement.created_at)
            )
        )


async def test_sync_creates_renews_is_idempotent_and_cancels_on_refund(env):
    admin, _ = await env.login("admin")
    buyer, bu = await env.login("buyer")
    staff, su = await env.login("agent")
    p = await subscription_product(env, admin)
    # a plain product is never turned into a service
    plain = await env.product(mode="manual", title="Plain")
    await delivered_order(env, buyer, plain, staff=staff)

    first = await delivered_order(env, buyer, p, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    d1 = datetime.fromisoformat(first["delivered_at"].replace("Z", "+00:00"))
    assert row.source == "auto" and row.status == "active" and row.product_id == p.id
    assert row.owner_id == su.id and row.buyer_id == bu.id and row.label == "Netflix Premium"
    assert row.starts_at == d1 and row.ends_at == add_months(d1, 1)
    assert row.email_reminders and row.telegram_reminders and row.version == 1
    assert await jobs.sync_subscriptions(env.res) == 0  # idempotent

    # second paid+delivered order: renewal extends from max(ends_at, delivered_at); qty periods
    second = await delivered_order(env, buyer, p, qty=2, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    assert row.ends_at == add_months(add_months(d1, 1), 2) and row.version == 2
    async with env.api_session() as db:
        ren = await db.scalar(
            select(ServiceRenewal).where(ServiceRenewal.order_id == uuid.UUID(second["id"]))
        )
        assert ren is not None and ren.entitlement_id == row.id and ren.confirmed_by == su.id
        assert await db.scalar(select(func.count(ServiceRenewal.id))) == 1
    assert await jobs.sync_subscriptions(env.res) == 0
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action IN ('service.created','service.renewed') "
            "AND actor_role='system' AND detail->>'reason'='auto_subscription'"
        )
        == 2
    )

    # the source order is refunded -> the auto service is cancelled (audited as system)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET status='refunded' WHERE id=:i"), {"i": uuid.UUID(first["id"])}
        )
        await db.commit()
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    assert row.status == "cancelled"
    assert await jobs.sync_subscriptions(env.res) == 0
    # a later order starts a fresh service (the cancelled one is not renewed)
    await delivered_order(env, buyer, p, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1
    assert len(await entitlements(env)) == 2


async def test_sync_ignores_old_orders_and_pins_tenants(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await subscription_product(env, admin)
    old = await delivered_order(env, buyer, p, staff=staff)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET delivered_at = now() - interval '60 days' WHERE id=:i"),
            {"i": uuid.UUID(old["id"])},
        )
        await db.commit()
    assert await jobs.sync_subscriptions(env.res) == 0

    # another tenant's subscription order only ever produces rows inside that tenant
    other = env.other_tenant_id
    _oa, oadmin = await env.login("admin", tenant_id=other)
    _ob, obuyer = await env.login("buyer", tenant_id=other)
    op = await env.product(mode="manual", title="Other sub", tenant_id=other)
    async with env.owner_session(other) as db:
        await db.execute(
            text("UPDATE products SET subscription = '{\"grace_days\": 1}'::jsonb WHERE id=:i"),
            {"i": op.id},
        )
        await db.execute(
            text(
                "INSERT INTO orders (id, tenant_id, order_no, buyer_id, product_id, title, qty, "
                "amount_minor, subtotal_minor, currency, status, delivery_mode, delivered_at) "
                "VALUES (:id, :t, 'SUBOTHER0001', :b, :p, 'x', 1, 100, 100, 'CNY', 'delivered', "
                "'manual', now())"
            ),
            {"id": uuid.uuid4(), "t": other, "b": obuyer.id, "p": op.id},
        )
        await db.commit()
    assert await jobs.sync_subscriptions(env.res) == 1
    assert await entitlements(env) == []
    (theirs,) = await entitlements(env, other)
    assert theirs.tenant_id == other and theirs.owner_id == oadmin.id and theirs.grace_days == 1


# ------------------------------------------------------------------ admin Services page


async def test_admin_create_by_order_no_prefills_and_list_is_enriched(env):
    admin, au = await env.login("admin")
    buyer, bu = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await subscription_product(
        env, admin, grace_days=3, email_reminders=True, telegram_reminders=False
    )
    order = await delivered_order(env, buyer, p, staff=staff)
    r = await admin.post(f"{API}/admin/services", json={"order_no": order["order_no"]})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["buyer_id"] == str(bu.id) and item["order_id"] == order["id"]
    assert item["product_id"] == str(p.id) and item["source"] == "manual"
    assert item["label"] == "Netflix Premium" and item["grace_days"] == 3
    assert item["telegram_reminders"] is False and item["email_reminders"] is True
    assert (
        await admin.post(f"{API}/admin/services", json={"order_no": order["order_no"]})
    ).status_code == 409
    assert (await admin.post(f"{API}/admin/services", json={"order_no": "NOPE"})).status_code == 404
    assert (await admin.post(f"{API}/admin/services", json={"label": "x"})).status_code in (
        400,
        422,
    )
    # a foreign tenant cannot register our order by number
    foreign, fadmin = await env.login("admin", tenant_id=env.other_tenant_id)
    r = await foreign.post(f"{API}/admin/services", json={"order_no": order["order_no"]})
    assert r.status_code in (401, 404)
    assert (
        await admin.post(f"{API}/admin/services/{item['id']}/activate", json={})
    ).status_code == 200

    rows = (await admin.get(f"{API}/admin/services", params={"q": order["order_no"][:8]})).json()[
        "items"
    ]
    assert [x["id"] for x in rows] == [item["id"]]
    row = rows[0]
    assert row["order_no"] == order["order_no"] and row["product_title"] == "Netflix Premium"
    assert row["buyer_email"] == bu.email and row["after_sales_open"] is False
    assert row["days_left"] in (30, 31)
    by_email = (await admin.get(f"{API}/admin/services", params={"q": bu.email})).json()["items"]
    assert len(by_email) == 1
    assert (await admin.get(f"{API}/admin/services", params={"q": "zzz-none"})).json()[
        "items"
    ] == []
    seen = await foreign.get(f"{API}/admin/services", params={"q": order["order_no"]})
    assert seen.status_code in (401, 404) or seen.json()["items"] == []

    # an open after-sales request on a linked order raises the flag
    r = await buyer.post(
        f"{API}/orders/{order['id']}/after-sales",
        json={"reason": "invalid_code", "description": "broken", "attachment_ids": []},
    )
    assert r.status_code in (200, 201), r.text
    row = (await admin.get(f"{API}/admin/services", params={"q": order["order_no"]})).json()[
        "items"
    ][0]
    assert row["after_sales_open"] is True
    d = (await admin.get(f"{API}/admin/services/{item['id']}")).json()
    assert [o["order_no"] for o in d["orders"]] == [order["order_no"]]
    assert d["orders"][0]["kind"] == "source" and d["orders"][0]["after_sales_status"] == "open"
    assert (
        d["orders"][0]["amount"]
        and d["orders"][0]["currency"] == "CNY"
        and d["orders"][0]["paid_at"]
    )
    assert (await foreign.get(f"{API}/admin/services/{item['id']}")).status_code in (401, 404)
    assert fadmin.id != au.id


async def test_renewal_candidates_are_scoped_and_shrink_after_use(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    other_buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await env.product(mode="manual", title="Svc", price=1000, floor=100)
    src = await delivered_order(env, buyer, p, staff=staff)
    r = await admin.post(f"{API}/admin/services", json={"order_no": src["order_no"]})
    sid = r.json()["id"]
    await admin.post(f"{API}/admin/services/{sid}/activate", json={})
    again = await delivered_order(env, buyer, p, staff=staff)
    other_product = await env.product(mode="manual", title="Different", price=1000, floor=100)
    await delivered_order(env, buyer, other_product, staff=staff)
    await delivered_order(env, other_buyer, p, staff=staff)
    got = (await admin.get(f"{API}/admin/services/{sid}/renewal-candidates")).json()["items"]
    assert [c["order_no"] for c in got] == [
        again["order_no"]
    ]  # source/other buyer/product excluded
    assert got[0]["order_id"] == again["id"] and got[0]["amount"] and got[0]["paid_at"]
    r = await admin.post(
        f"{API}/admin/services/{sid}/renewals", json={"order_id": again["id"], "reason": "paid"}
    )
    assert r.status_code == 200, r.text
    assert (await admin.get(f"{API}/admin/services/{sid}/renewal-candidates")).json()["items"] == []
    d = (await admin.get(f"{API}/admin/services/{sid}")).json()
    assert [o["kind"] for o in d["orders"]] == ["source", "renewal"]
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.get(f"{API}/admin/services/{sid}/renewal-candidates")).status_code in (
        401,
        404,
    )
    assert (await buyer.get(f"{API}/admin/services/{sid}/renewal-candidates")).status_code == 403


# ------------------------------------------------------------------ buyer view


async def test_buyer_order_view_shows_service_only_to_its_owner(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    stranger, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await env.product(mode="manual", title="Svc", price=1000, floor=100)
    plain = await delivered_order(
        env, buyer, await env.product(mode="manual", title="N"), staff=staff
    )
    src = await delivered_order(env, buyer, p, staff=staff)
    assert (await buyer.get(f"{API}/orders/{src['id']}")).json()["service"] is None
    r = await admin.post(f"{API}/admin/services", json={"order_no": src["order_no"]})
    sid = r.json()["id"]
    await admin.post(f"{API}/admin/services/{sid}/activate", json={})
    got = (await buyer.get(f"{API}/orders/{src['id']}")).json()["service"]
    assert got["status"] == "active" and got["starts_at"] and got["ends_at"]
    renewal = await delivered_order(env, buyer, p, staff=staff)
    await admin.post(
        f"{API}/admin/services/{sid}/renewals", json={"order_id": renewal["id"], "reason": "paid"}
    )
    ends = (await buyer.get(f"{API}/orders/{renewal['id']}")).json()["service"]["ends_at"]
    assert ends == (await buyer.get(f"{API}/orders/{src['id']}")).json()["service"]["ends_at"]
    assert (await buyer.get(f"{API}/orders/{plain['id']}")).json()["service"] is None
    listed = {o["id"]: o["service"] for o in (await buyer.get(f"{API}/orders")).json()["items"]}
    assert listed[src["id"]] and listed[renewal["id"]] and listed[plain["id"]] is None
    # another buyer can neither read the order nor its service
    assert (await stranger.get(f"{API}/orders/{src['id']}")).status_code == 404
    assert all(o["service"] is None for o in (await stranger.get(f"{API}/orders")).json()["items"])


async def test_sync_skips_when_two_open_services_match(env):
    """Security review PR #56: never guess which of several services to extend, never add more."""
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await subscription_product(env, admin)
    await delivered_order(env, buyer, p, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1  # auto service
    manual = await delivered_order(env, buyer, p, staff=staff)
    r = await admin.post(f"{API}/admin/services", json={"order_no": manual["order_no"]})
    assert r.status_code == 201, r.text  # staff registered a second, manual one
    await delivered_order(env, buyer, p, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 0
    assert len(await entitlements(env)) == 2
