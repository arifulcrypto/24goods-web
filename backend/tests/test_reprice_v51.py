"""review-v5.1 s1: staff reprice on promo / reseller-markup orders."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import NotFound
from app.modules.orders.admin import change_price
from tests.helpers import buy, pay_order
from tests.test_commerce_v4 import checkout, line, set_fee, set_settings
from tests.test_commerce_v5 import FEATURES, ledger_sum, make_promo, make_reseller

API = "/api/v1"


def price_url(order) -> str:
    return f"{API}/admin/orders/{order['id']}/price"


async def storefront_order(env, qty=2):
    await set_fee(env, 30)
    await set_settings(env, FEATURES)
    _rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=300)
    p = await env.product(mode="manual", price=1990, floor=1000)
    buyer, _ = await env.login("buyer")
    r = await buyer.post(
        f"{API}/orders",
        json={"product_id": str(p.id), "qty": qty},
        headers={"X-Storefront": "shopa"},
    )
    return buyer, r.json()


async def test_promo_order_reprice_releases_redemption(env):
    await set_fee(env, 30)
    admin, _ = await env.login("admin")
    await make_promo(admin)  # 10%
    p = await env.product(mode="manual", price=1990, floor=1000)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p, promo_code="save10")
    assert await env.scalar("SELECT used_count FROM promo_codes") == 1
    r = await admin.patch(price_url(o), json={"amount": "15.00", "reason": "deal"})
    assert r.status_code == 200, r.text
    n = r.json()
    # no discount, fee ceil(1500*3%)=45
    assert (n["subtotal"], n["fee"], n["amount"]) == ("15.00", "0.45", "15.45")
    assert n["discount_minor"] == 0 and n["version"] == o["version"] + 1
    assert await env.scalar("SELECT used_count FROM promo_codes") == 0
    assert await env.scalar("SELECT status FROM promo_redemptions") == "released"
    detail = await env.scalar(
        "SELECT detail->>'old_discount_minor' FROM audit_log WHERE action='order.price_changed'"
    )
    assert detail == "199"
    # still payable; promo not re-used
    await pay_order(env, buyer, n)
    assert await ledger_sum(env, "promo_discount") == 0
    assert await ledger_sum(env, "sales_revenue") == -1500


async def test_markup_recompute_and_remove(env):
    buyer, o = await storefront_order(env)  # unit 22.90 x2 = 45.80, markup 6.00, floor 20.00
    assert o["subtotal"] == "45.80"
    agent, _ = await env.login("agent")
    # keep markup: min(600, 4200-2000) = 600
    r = await agent.patch(price_url(o), json={"amount": "42.00", "reason": "x"})
    assert r.status_code == 200, r.text
    assert await env.scalar("SELECT reseller_markup_minor FROM orders") == 600
    # new subtotal 22.00 -> markup capped to 22.00-20.00 = 2.00
    r = await agent.patch(price_url(o), json={"amount": "22.00", "reason": "x"})
    assert r.status_code == 200
    assert await env.scalar("SELECT reseller_markup_minor FROM orders") == 200
    r = await agent.patch(
        price_url(o), json={"amount": "30.00", "reason": "x", "remove_markup": True}
    )
    assert r.status_code == 200
    assert await env.scalar("SELECT reseller_markup_minor FROM orders") == 0
    assert (
        await env.scalar(
            "SELECT detail->>'old_markup_minor' FROM audit_log WHERE action='order.price_changed' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        == "200"
    )
    await pay_order(env, buyer, r.json())
    assert await ledger_sum(env, "sales_revenue") == -3000


async def test_below_floor_and_checkout_member_refused(env):
    _b, o = await storefront_order(env)
    agent, _ = await env.login("agent")
    r = await agent.patch(price_url(o), json={"amount": "19.99", "reason": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reprice_below_floor"
    p2 = await env.product(mode="manual", price=1990, floor=100, title="Two")
    buyer, _ = await env.login("buyer")
    cr = await checkout(buyer, [line(p2), line(p2, 2)])
    assert cr.status_code == 200, cr.text
    oid = await env.scalar("SELECT id FROM orders WHERE checkout_id IS NOT NULL LIMIT 1")
    r = await agent.patch(
        f"{API}/admin/orders/{oid}/price", json={"amount": "10.00", "reason": "x"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "reprice_checkout_member"


async def test_reprice_cross_tenant_staff_404(env):
    _b, o = await storefront_order(env)
    other_agent, other_user = await env.login("agent", tenant_id=env.other_tenant_id)
    # the shared host maps to the default tenant: a foreign staff session is rejected outright
    r = await other_agent.patch(price_url(o), json={"amount": "30.00", "reason": "x"})
    assert r.status_code == 401
    # and even past the session layer the service filters by tenant -> NotFound
    async with env.api_session(env.other_tenant_id) as db:
        with pytest.raises(NotFound):
            await change_price(db, uuid.UUID(o["id"]), "30.00", "x", other_user, None)
    r = await (await env.login("agent"))[0].patch(
        f"{API}/admin/orders/{uuid.uuid4()}/price", json={"amount": "30.00", "reason": "x"}
    )
    assert r.status_code == 404
    assert await env.scalar("SELECT reseller_markup_minor FROM orders") == 600
