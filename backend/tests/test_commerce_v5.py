"""v5 commerce: promo codes, reseller storefronts, sign-up, ledger, payouts (docs/review-v5.md)."""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import storefront
from app.models import PromoCode, Reseller
from app.modules.orders.service import expire_pending
from app.modules.promo.service import PromoError, PromoLine, allocate, compute
from tests.helpers import buy, pay_order, simulate_gateway_payment
from tests.test_commerce_v4 import checkout, line, set_fee, set_settings

API = "/api/v1"
FEATURES = {"features": {"resellers": True}}


# ------------------------------------------------------------------ helpers
def mk_promo(**kw) -> PromoCode:
    base = dict(
        code="TEST10",
        kind="percent",
        value=100,
        currency="CNY",
        min_subtotal_minor=0,
        max_discount_minor=None,
        product_ids=[],
        category_slugs=[],
        used_count=0,
    )
    base.update(kw)
    return PromoCode(**base)


def pl(sub, cat="gift_cards", pid=None):
    return PromoLine(pid or uuid.uuid4(), cat, sub)


async def make_promo(admin, **over):
    body = {"code": "SAVE10", "kind": "percent", "value": 100}
    body.update(over)
    r = await admin.post(f"{API}/admin/promo-codes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def ledger_sum(env, account: str) -> int:
    """Sum of an account over the PAYMENT/refund/payout txns (the gateway-cost txn that also
    touches gateway_clearing is excluded: it is v4 behaviour)."""
    return int(
        await env.scalar(
            "SELECT coalesce(sum(amount_minor),0) FROM ledger_entries WHERE account=:a "
            "AND coalesce(memo,'') NOT LIKE 'zpay fee%'",
            a=account,
        )
    )


async def assert_ledger_balanced(env) -> None:
    assert (
        await env.scalar(
            "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
            "HAVING sum(amount_minor) <> 0) x"
        )
        == 0
    )


async def make_reseller(env, user, slug="shopa", status="active", kind="percent", value=100):
    async with env.owner_session() as db:
        r = Reseller(
            tenant_id=env.tenant_id,
            user_id=user.id,
            slug=slug,
            store_name=f"Store {slug}",
            status=status,
            markup_kind=kind,
            markup_value=value,
        )
        db.add(r)
        await db.commit()
        storefront.invalidate()
        return r


# ------------------------------------------------------------------ pure promo maths
def test_percent_floor_fixed_cap_min_and_scope():
    p = mk_promo(kind="percent", value=125)  # 12.5%
    total, elig, alloc = compute(p, [pl(999)], "CNY")
    assert (total, elig, alloc) == (124, 999, [124])  # floor(124.875)
    p = mk_promo(kind="fixed", value=5000)
    assert compute(p, [pl(1990)], "CNY")[0] == 1989  # fixed capped to subtotal-1
    p = mk_promo(kind="percent", value=500, max_discount_minor=300)
    assert compute(p, [pl(1990)], "CNY")[0] == 300  # cap
    p = mk_promo(min_subtotal_minor=2000)
    with pytest.raises(PromoError) as e:
        compute(p, [pl(1990)], "CNY")
    assert e.value.code == "promo_min_subtotal"
    a, b = uuid.uuid4(), uuid.uuid4()
    p = mk_promo(kind="percent", value=100, product_ids=[a])
    total, elig, alloc = compute(p, [pl(1000, pid=a), pl(5000, pid=b)], "CNY")
    assert (total, elig, alloc) == (100, 1000, [100, 0])
    p = mk_promo(category_slugs=["ai_tools"])
    with pytest.raises(PromoError) as e:
        compute(p, [pl(1000)], "CNY")
    assert e.value.code == "promo_not_applicable"
    with pytest.raises(PromoError):
        compute(mk_promo(currency="USD"), [pl(1000)], "CNY")


def test_percent_100_never_zeroes_an_order():
    p = mk_promo(kind="percent", value=1000)
    total, _e, alloc = compute(p, [pl(1000), pl(1)], "CNY")
    assert alloc[1] == 0 and alloc[0] == 999  # a 1-fen line can't take a discount
    assert total == 999


def test_allocation_invariants_random():
    rng = random.Random(7)
    for _ in range(3000):
        n = rng.randint(1, 8)
        subs = [rng.randint(2, 5000) for _ in range(n)]
        caps = [s - 1 for s in subs]
        total = rng.randint(1, sum(caps))
        out = allocate(total, subs, caps)
        assert sum(out) == total
        assert all(0 <= o <= c for o, c in zip(out, caps, strict=True))


# ------------------------------------------------------------------ buy now with promo
async def test_buy_now_promo_math_fee_on_discounted_subtotal_and_ledger(env):
    await set_fee(env, 30)
    admin, _ = await env.login("admin")
    promo = await make_promo(admin)  # 10% off
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    v = (
        await buyer.post(
            f"{API}/promo/validate",
            json={"code": "save10", "items": [{"product_id": str(p.id), "qty": 1}]},
        )
    ).json()
    assert v["discount_minor"] == 199 and v["eligible_subtotal_minor"] == 1990
    o = await buy(buyer, p, promo_code="save10")
    # subtotal 1990, discount 199, fee ceil(1791*3%)=54 (53.73), amount 1845
    assert (o["subtotal"], o["discount"], o["fee"], o["amount"]) == (
        "19.90",
        "1.99",
        "0.54",
        "18.45",
    )
    assert o["discount_minor"] == 199 and o["promo_code"] == "SAVE10" and o["kind"] == "goods"
    assert await env.scalar("SELECT used_count FROM promo_codes") == 1
    assert await env.scalar("SELECT status FROM promo_redemptions") == "reserved"
    await pay_order(env, buyer, o)
    assert await env.scalar("SELECT status FROM promo_redemptions") == "used"
    assert await ledger_sum(env, "gateway_clearing:zpay") == 1845
    assert await ledger_sum(env, "sales_revenue") == -1990
    assert await ledger_sum(env, "promo_discount") == 199
    assert await ledger_sum(env, "surcharge_revenue") == -54
    await assert_ledger_balanced(env)
    assert promo["code"] == "SAVE10"


async def test_promo_errors_and_per_user_limit(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    items = [{"product_id": str(p.id), "qty": 1}]

    async def val(code):
        r = await buyer.post(f"{API}/promo/validate", json={"code": code, "items": items})
        return r.status_code, (r.json().get("error") or {}).get("code")

    assert await val("NOPE") == (422, "promo_invalid")
    now = datetime.now(timezone.utc)
    await make_promo(admin, code="FUTURE", starts_at=(now + timedelta(days=1)).isoformat())
    await make_promo(
        admin,
        code="PAST",
        ends_at=(now - timedelta(hours=1)).isoformat(),
        starts_at=(now - timedelta(days=2)).isoformat(),
    )
    await make_promo(admin, code="BIG", min_subtotal_minor=999999)
    await make_promo(admin, code="OFF", enabled=False)
    assert await val("FUTURE") == (422, "promo_not_started")
    assert await val("PAST") == (422, "promo_expired")
    assert await val("BIG") == (422, "promo_min_subtotal")
    assert await val("OFF") == (422, "promo_invalid")
    await make_promo(admin, code="ONCE")  # per_user_limit 1
    await buy(buyer, p, promo_code="ONCE")
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "promo_code": "ONCE"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "promo_user_limit"
    # another user is fine; usage_limit exhausts
    await make_promo(admin, code="LIM1", usage_limit=1)
    b2, _ = await env.login("buyer")
    b3, _ = await env.login("buyer")
    await buy(b2, p, promo_code="LIM1")
    r = await b3.post(f"{API}/orders", json={"product_id": str(p.id), "promo_code": "LIM1"})
    assert r.json()["error"]["code"] == "promo_exhausted"


async def test_usage_limit_is_concurrency_safe(env):
    admin, _ = await env.login("admin")
    await make_promo(admin, code="RACE", usage_limit=2)
    p = await env.product(mode="manual", price=1990, floor=100)
    buyers = [(await env.login("buyer"))[0] for _ in range(6)]
    rs = await asyncio.gather(
        *[
            b.post(f"{API}/orders", json={"product_id": str(p.id), "promo_code": "RACE"})
            for b in buyers
        ]
    )
    assert sorted(r.status_code for r in rs).count(200) == 2
    assert await env.scalar("SELECT used_count FROM promo_codes WHERE code='RACE'") == 2
    assert await env.scalar("SELECT count(*) FROM promo_redemptions") == 2


async def test_promo_released_on_cancel_and_expiry_and_checkout_cancel(env):
    admin, _ = await env.login("admin")
    await make_promo(admin, code="REL", per_user_limit=5)
    p = await env.product(mode="manual", price=1990, floor=100)
    q = await env.product(mode="manual", price=990, floor=100)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")

    async def state():
        return (
            await env.scalar("SELECT used_count FROM promo_codes WHERE code='REL'"),
            await env.scalar(
                "SELECT string_agg(status, ',' ORDER BY created_at) FROM promo_redemptions"
            ),
        )

    o = await buy(buyer, p, promo_code="REL")
    assert (await state())[0] == 1
    assert (await buyer.post(f"{API}/orders/{o['id']}/cancel", json={})).status_code == 200
    assert await state() == (0, "released")
    # staff close of a single promo order
    o = await buy(buyer, p, promo_code="REL")
    r = await staff.post(f"{API}/admin/orders/{o['id']}/cancel", json={"reason": "test"})
    assert r.status_code == 200, r.text
    assert (await state())[0] == 0
    # expiry of a single order
    o = await buy(buyer, p, promo_code="REL")
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET expires_at = now() - interval '1 min' WHERE id = :i"),
            {"i": o["id"]},
        )
        await db.commit()
    assert await expire_pending(env.res.maker) >= 1
    assert (await state())[0] == 0
    # checkout: cancel then expire
    r = await checkout(buyer, [line(p), line(q)], promo_code="REL")
    co = r.json()["checkout"]
    assert (await state())[0] == 1
    assert (
        await buyer.post(f"{API}/checkouts/{co['checkout_no']}/cancel", json={})
    ).status_code == 200
    assert (await state())[0] == 0
    r = await checkout(buyer, [line(p), line(q)], promo_code="REL")
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE checkouts SET expires_at = now() - interval '1 min' "
                "WHERE status='pending_payment'"
            )
        )
        await db.commit()
    assert await expire_pending(env.res.maker) >= 2
    assert (await state())[0] == 0
    assert await env.scalar("SELECT count(*) FROM promo_redemptions WHERE status<>'released'") == 0


async def test_checkout_promo_allocation_fee_and_settlement(env):
    await set_fee(env, 30)
    admin, _ = await env.login("admin")
    await make_promo(admin, code="CART", kind="percent", value=150, per_user_limit=3)
    a = await env.product(mode="manual", price=1990, floor=100)
    b = await env.product(mode="manual", price=333, floor=100)
    c = await env.product(mode="manual", price=777, floor=100)
    buyer, _ = await env.login("buyer")
    items = [line(a, 2), line(b), line(c, 3)]
    quote = (
        await buyer.post(f"{API}/cart/quote", json={"items": items, "promo_code": "cart"})
    ).json()
    subs = [3980, 333, 2331]
    total_disc = sum(subs) * 150 // 1000
    assert (
        quote["discount_minor"] == total_disc == sum(ln["discount_minor"] for ln in quote["lines"])
    )
    assert quote["promo"] == {"code": "CART", "discount_minor": total_disc}
    r = await checkout(buyer, items, promo_code="cart")
    assert r.status_code == 200, r.text
    body = r.json()
    orders = body["orders"]
    discs = [o["discount_minor"] for o in orders]
    assert sum(discs) == total_disc
    assert all(0 <= d < s for d, s in zip(discs, subs, strict=True))
    # fee per order on the discounted subtotal
    exp_amount = 0
    for s, d in zip(subs, discs, strict=True):
        net = s - d
        exp_amount += net + (net * 30 + 999) // 1000
    assert body["checkout"]["amount"] == f"{exp_amount / 100:.2f}"
    assert quote["total"] == body["checkout"]["amount"]
    assert (
        await env.scalar(
            "SELECT count(*) FROM orders "
            "WHERE amount_minor <> subtotal_minor - discount_minor + fee_minor"
        )
        == 0
    )
    assert await env.scalar("SELECT status FROM promo_redemptions") == "reserved"
    pay = await buyer.post(
        f"{API}/checkouts/{body['checkout']['checkout_no']}/pay", json={"channel": "zpay_alipay"}
    )
    assert (await simulate_gateway_payment(env, pay.json()["pay_url"])).status_code == 302
    assert await env.scalar("SELECT status FROM promo_redemptions") == "used"
    assert await env.scalar("SELECT used_count FROM promo_codes") == 1
    assert await ledger_sum(env, "promo_discount") == total_disc
    assert await ledger_sum(env, "gateway_clearing:zpay") == exp_amount
    assert await ledger_sum(env, "sales_revenue") == -sum(subs)
    await assert_ledger_balanced(env)


async def test_promo_not_on_quote_orders_and_cross_tenant_and_toggle(env):
    admin, _ = await env.login("admin")
    await make_promo(admin, code="XTX")
    p = await env.product(mode="manual", price=1990, floor=100)
    # a promo of ANOTHER tenant is unknown here
    async with env.owner_session(env.other_tenant_id) as db:
        db.add(mk_promo(code="OTHERT", tenant_id=env.other_tenant_id))
        await db.commit()
    buyer, _ = await env.login("buyer")
    items = [{"product_id": str(p.id), "qty": 1}]
    r = await buyer.post(f"{API}/promo/validate", json={"code": "OTHERT", "items": items})
    assert r.json()["error"]["code"] == "promo_invalid"
    # admin of tenant 1 cannot see / patch another tenant's promo
    async with env.owner_session(env.other_tenant_id) as db:
        pid = await db.scalar(text("SELECT id FROM promo_codes WHERE code='OTHERT'"))
    r = await admin.patch(f"{API}/admin/promo-codes/{pid}", json={"enabled": False})
    assert r.status_code == 404
    # toggle off
    await set_settings(env, {"features": {"promo_codes": False}})
    r = await buyer.post(f"{API}/promo/validate", json={"code": "XTX", "items": items})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "promo_code": "XTX"})
    assert r.status_code == 403
    await set_settings(env, {"features": {"cart": False}})
    r = await buyer.post(f"{API}/cart/quote", json={"items": [line(p)]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    assert (await checkout(buyer, [line(p)])).status_code == 403


async def test_admin_generator_list_and_redemptions(env):
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")
    code = (await admin.get(f"{API}/admin/promo-codes/generate")).json()["code"]
    assert len(code) == 8
    assert (await agent.get(f"{API}/admin/promo-codes")).status_code == 403
    pr = await make_promo(admin, code=code, kind="fixed", value=100)
    auto = await admin.post(f"{API}/admin/promo-codes", json={"kind": "fixed", "value": 50})
    assert auto.status_code == 200 and len(auto.json()["code"]) == 8
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    await buy(buyer, p, promo_code=code.lower())
    reds = (await admin.get(f"{API}/admin/promo-codes/{pr['id']}/redemptions")).json()
    assert reds["total"] == 1 and reds["items"][0]["discount_minor"] == 100
    assert (await admin.get(f"{API}/admin/promo-codes")).json()["total"] == 2


# ------------------------------------------------------------------ reseller pricing
async def test_storefront_pricing_percent_fixed_override_hidden_clamp(env):
    await set_settings(env, {**FEATURES, "resellers": {"max_markup_permille": 300}})
    _rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="percent", value=105)  # 10.5%
    p1 = await env.product(mode="manual", price=1999, floor=100, title="One")
    p2 = await env.product(mode="manual", price=1000, floor=100, title="Two")
    p3 = await env.product(mode="manual", price=1000, floor=100, title="Three")
    rid = await env.scalar("SELECT id FROM resellers")
    async with env.owner_session() as db:
        for pid, kind, val, hid in ((p2.id, "fixed", 250, False), (p3.id, "percent", 900, False)):
            await db.execute(
                text(
                    "INSERT INTO reseller_prices (tenant_id, reseller_id, product_id, markup_kind, "
                    "markup_value, hidden) VALUES (:t, :r, :p, :k, :v, :h)"
                ),
                {"t": env.tenant_id, "r": rid, "p": pid, "k": kind, "v": val, "h": hid},
            )
        await db.commit()
    anon = env.client()
    sf = {"X-Storefront": "shopa"}
    items = {
        i["title"]: i["price"]
        for i in (await anon.get(f"{API}/products", headers=sf)).json()["items"]
    }
    # ceil(1999*105/1000)=210 (209.895) ; fixed +250 ; 900 permille clamped to 300 -> +300
    assert items == {"One": "22.09", "Two": "12.50", "Three": "13.00"}
    base = {i["title"]: i["price"] for i in (await anon.get(f"{API}/products")).json()["items"]}
    assert base["One"] == "19.99"
    d = (await anon.get(f"{API}/products/{p1.id}", headers=sf)).json()
    assert d["price"] == "22.09"
    # host based resolution
    host_client = env.client("shopa.kesiv.com")
    assert (await host_client.get(f"{API}/products/{p1.id}")).json()["price"] == "22.09"
    site = (await host_client.get(f"{API}/site")).json()
    assert site["storefront"]["slug"] == "shopa" and site["storefront"]["white_label"] is True
    assert site["brand"] == "Store shopa"
    assert (await anon.get(f"{API}/site")).json()["storefront"] is None
    # hidden product disappears (list, detail, cart)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE reseller_prices SET hidden=true WHERE product_id=:p"), {"p": p2.id}
        )
        await db.commit()
    titles = [i["title"] for i in (await anon.get(f"{API}/products", headers=sf)).json()["items"]]
    assert "Two" not in titles
    assert (await anon.get(f"{API}/products/{p2.id}", headers=sf)).status_code == 404
    q = (await anon.post(f"{API}/cart/quote", json={"items": [line(p2)]}, headers=sf)).json()
    assert q["lines"][0]["available"] is False and q["subtotal"] == "0.00"
    # suspended / unknown / feature off -> main store
    async with env.owner_session() as db:
        await db.execute(text("UPDATE resellers SET status='suspended'"))
        await db.commit()
    assert (await anon.get(f"{API}/products/{p1.id}", headers=sf)).json()["price"] == "19.99"
    await anon.get(f"{API}/products/{p1.id}", headers={"X-Storefront": "nope"})
    async with env.owner_session() as db:
        await db.execute(text("UPDATE resellers SET status='active'"))
        await db.commit()
    await set_settings(env, {"features": {"resellers": False}})
    assert (await anon.get(f"{API}/products/{p1.id}", headers=sf)).json()["price"] == "19.99"


async def test_storefront_order_markup_ledger_refund_and_visibility(env):
    await set_fee(env, 30)
    await set_settings(env, FEATURES)
    rbuyer, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=300)
    other_b, ou = await env.login("buyer")
    await make_reseller(env, ou, "shopb", kind="fixed", value=100)
    p = await env.product(mode="manual", price=1990, floor=100)
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    hs = {"X-Storefront": "shopa"}
    r = await buyer.post(f"{API}/orders", json={"product_id": str(p.id), "qty": 2}, headers=hs)
    o = r.json()
    # unit 22.90 x2 = 45.80 subtotal (markup 6.00); fee ceil(4580*3%)=138 (137.4)
    assert (o["subtotal"], o["fee"], o["amount"]) == ("45.80", "1.38", "47.18")
    assert o["reseller"] == {"store_name": "Store shopa"}
    # promo is not applicable on storefronts
    await make_promo(admin, code="NOSF")
    r = await buyer.post(
        f"{API}/orders", json={"product_id": str(p.id), "promo_code": "NOSF"}, headers=hs
    )
    assert r.json()["error"]["code"] == "promo_not_applicable"
    await pay_order(env, buyer, o)
    rid = await env.scalar("SELECT id FROM resellers WHERE slug='shopa'")
    assert await ledger_sum(env, "sales_revenue") == -(4580 - 600)
    assert await ledger_sum(env, f"reseller_payable:{rid}") == -600
    assert await ledger_sum(env, "surcharge_revenue") == -138
    assert await ledger_sum(env, "gateway_clearing:zpay") == 4718
    await assert_ledger_balanced(env)
    # reseller center
    ro = (await rbuyer.get(f"{API}/reseller/orders")).json()
    assert ro["total"] == 1 and ro["items"][0]["markup_minor"] == 600
    assert "buyer_input" not in ro["items"][0] and "***" in ro["items"][0]["buyer"]
    # the other reseller sees nothing of it
    assert (await other_b.get(f"{API}/reseller/orders")).json()["total"] == 0
    st = (await rbuyer.get(f"{API}/reseller/stats")).json()
    assert st["today"] == {"orders": 1, "earnings_minor": 600}
    assert st["balances"]["pending_minor"] == 600 and st["balances"]["available_minor"] == 0
    # admin sees reseller fields + filter
    lst = (await admin.get(f"{API}/admin/orders", params={"reseller_id": str(rid)})).json()
    assert lst["total"] == 1
    it = lst["items"][0]
    assert it["reseller"]["slug"] == "shopa" and it["reseller_markup_minor"] == 600
    # deliver -> refund mirrors every line
    assert (
        await staff.post(f"{API}/admin/orders/{o['id']}/deliver", json={"text": "ok"})
    ).status_code == 200
    t = await buyer.post(
        f"{API}/orders/{o['id']}/after-sales",
        json={"reason": "invalid_code", "description": "x", "attachment_ids": []},
    )
    assert t.status_code == 200, t.text
    r = await admin.post(
        f"{API}/admin/after-sales/{t.json()['id']}/action", json={"action": "refund"}
    )
    assert r.status_code == 200, r.text
    for acct in (
        "sales_revenue",
        f"reseller_payable:{rid}",
        "surcharge_revenue",
        "gateway_clearing:zpay",
    ):
        assert await ledger_sum(env, acct) == 0, acct
    await assert_ledger_balanced(env)
    # buyer on main store pays base price (header cannot be used to lower)
    o2 = await buy(buyer, p)
    assert o2["subtotal"] == "19.90" and o2["reseller"] is None
    # staff cannot change price of... (reseller order markup is clamped) -> see below
    o3 = (await buyer.post(f"{API}/orders", json={"product_id": str(p.id)}, headers=hs)).json()
    r = await staff.patch(
        f"{API}/admin/orders/{o3['id']}/price", json={"amount": "1.00", "reason": "deal"}
    )
    assert r.status_code == 200, r.text
    assert (
        await env.scalar("SELECT reseller_markup_minor FROM orders WHERE id=:i", i=o3["id"]) == 100
    )


async def test_conversation_gets_reseller_id(env):
    await set_settings(env, FEATURES)
    _, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa")
    buyer, _ = await env.login("buyer")
    r = await buyer.post(f"{API}/conversations", json={}, headers={"X-Storefront": "shopa"})
    assert r.status_code == 200
    assert await env.scalar("SELECT count(*) FROM conversations WHERE reseller_id IS NOT NULL") == 1


# ------------------------------------------------------------------ sign-up
async def test_signup_pay_activates_store_and_books_ledger(env):
    await set_fee(env, 30)
    await set_settings(env, {**FEATURES, "resellers": {"signup_fee_minor": 9900}})
    buyer, _ = await env.login("buyer")
    r = await buyer.post(f"{API}/reseller/apply", json={"slug": "My-Shop", "store_name": "My Shop"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "pending_payment" and me["signup_order_no"]
    so = me["signup_order"]
    assert so["kind"] == "reseller_signup" and so["delivery_mode"] == "system"
    assert (so["subtotal"], so["fee"], so["amount"]) == ("99.00", "2.97", "101.97")
    # not active yet -> main store pricing
    assert (await buyer.get(f"{API}/reseller/me")).json()["status"] == "pending_payment"
    # re-apply re-uses the order
    r2 = await buyer.post(
        f"{API}/reseller/apply", json={"slug": "my-shop", "store_name": "My Shop 2"}
    )
    assert r2.json()["signup_order"]["order_no"] == so["order_no"]
    await pay_order(env, buyer, so)
    me = (await buyer.get(f"{API}/reseller/me")).json()
    assert me["status"] == "active" and me["store_url"] == "https://my-shop.kesiv.com/"
    v = (await buyer.get(f"{API}/orders/{so['id']}")).json()
    assert v["status"] == "completed"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
    assert await ledger_sum(env, "reseller_signup_revenue") == -9900
    assert await ledger_sum(env, "surcharge_revenue") == -297
    assert await ledger_sum(env, "gateway_clearing:zpay") == 10197
    assert await ledger_sum(env, "sales_revenue") == 0
    await assert_ledger_balanced(env)
    # cannot apply again / signup orders have no after-sales
    r = await buyer.post(f"{API}/reseller/apply", json={"slug": "other-shop", "store_name": "X"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "reseller_exists"
    # the store works through the header now
    p = await env.product(mode="manual", price=1000, floor=100)
    b2, _ = await env.login("buyer")
    o = (
        await b2.post(
            f"{API}/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "my-shop"}
        )
    ).json()
    assert o["reseller"]["store_name"] == "My Shop 2" and o["subtotal"] == "11.00"


async def test_signup_validation_free_fee_and_slug_rules(env):
    await set_settings(env, {**FEATURES, "resellers": {"signup_fee_minor": 0}})
    buyer, _ = await env.login("buyer")
    assert (
        await buyer.post(f"{API}/reseller/apply", json={"slug": "admin", "store_name": "x"})
    ).json()["error"]["code"] == "slug_reserved"
    assert (
        await buyer.post(f"{API}/reseller/apply", json={"slug": "Bad_Slug!", "store_name": "x"})
    ).status_code == 422
    r = await buyer.post(f"{API}/reseller/apply", json={"slug": "freeshop", "store_name": "Free"})
    assert r.json()["status"] == "active" and r.json()["signup_order"] is None
    b2, _ = await env.login("buyer")
    r = await b2.post(f"{API}/reseller/apply", json={"slug": "freeshop", "store_name": "Dup"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "slug_taken"
    await set_settings(env, {"features": {"resellers": False}})
    assert (
        await b2.post(f"{API}/reseller/apply", json={"slug": "newone", "store_name": "N"})
    ).status_code == 403
    assert (await buyer.get(f"{API}/reseller/me")).status_code == 403


async def test_admin_activate_suspend_and_markup_limits(env):
    await set_settings(env, {**FEATURES, "resellers": {"signup_fee_minor": 5000}})
    buyer, _ = await env.login("buyer")
    admin, _ = await env.login("admin")
    await buyer.post(f"{API}/reseller/apply", json={"slug": "waive", "store_name": "W"})
    rid = await env.scalar("SELECT id FROM resellers")
    r = await admin.patch(f"{API}/admin/resellers/{rid}", json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert await env.scalar("SELECT status FROM orders WHERE kind='reseller_signup'") == "cancelled"
    r = await admin.patch(f"{API}/admin/resellers/{rid}", json={"status": "suspended"})
    assert r.json()["status"] == "suspended"
    r = await admin.patch(f"{API}/admin/resellers/{rid}", json={"status": "pending_payment"})
    assert r.status_code == 422
    r = await admin.patch(
        f"{API}/admin/resellers/{rid}", json={"markup_kind": "percent", "markup_value": 99999}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "markup_too_high"
    assert (
        await admin.get(f"{API}/admin/resellers", params={"status": "suspended", "q": "waive"})
    ).json()["total"] == 1
    r = await buyer.put(f"{API}/reseller/products/{uuid.uuid4()}", json={"hidden": True})
    assert r.status_code == 403  # suspended


# ------------------------------------------------------------------ reseller center + payouts
async def test_reseller_products_overrides_and_payout_ledger(env):
    await set_settings(
        env, {**FEATURES, "resellers": {"settle_after_hours": 0, "min_payout_minor": 500}}
    )
    rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=400)
    p = await env.product(mode="manual", price=1000, floor=100, title="Item")
    # product list with override / reset
    lst = (await rb.get(f"{API}/reseller/products")).json()
    it = lst["items"][0]
    assert it["base_price"] == "10.00" and it["price"] == "14.00" and it["custom"] is False
    assert (
        await rb.put(
            f"{API}/reseller/products/{p.id}", json={"markup_kind": "fixed", "markup_value": 700}
        )
    ).status_code == 204
    it = (await rb.get(f"{API}/reseller/products")).json()["items"][0]
    assert it["price"] == "17.00" and it["custom"] is True
    r = await rb.put(f"{API}/reseller/products/{p.id}", json={"markup_value": 99999999})
    assert r.status_code == 422 and r.json()["error"]["code"] == "markup_too_high"
    assert (await rb.delete(f"{API}/reseller/products/{p.id}")).status_code == 204
    assert (await rb.get(f"{API}/reseller/products")).json()["items"][0]["custom"] is False
    # PATCH me: own logo only
    r = await rb.patch(f"{API}/reseller/me", json={"logo_url": "https://evil.example/x.png"})
    assert r.status_code == 422
    r = await rb.patch(
        f"{API}/reseller/me",
        json={
            "store_name": "Renamed",
            "payout_account": {"method": "alipay", "name": "A", "account": "a@b.c"},
        },
    )
    assert r.status_code == 200 and r.json()["store_name"] == "Renamed"
    # earn: buy through the store, deliver, confirm -> completed -> available (settle 0h)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    o = (
        await buyer.post(
            f"{API}/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "shopa"}
        )
    ).json()
    await pay_order(env, buyer, o)
    await staff.post(f"{API}/admin/orders/{o['id']}/deliver", json={"text": "ok"})
    assert (await buyer.post(f"{API}/orders/{o['id']}/confirm")).status_code == 200
    bal = (await rb.get(f"{API}/reseller/me")).json()["balances"]
    assert bal == {"pending_minor": 0, "available_minor": 400, "paid_minor": 0}
    # payout rules
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 100})
    assert r.json()["error"]["code"] == "payout_below_minimum"
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 600})
    assert r.json()["error"]["code"] == "payout_below_minimum" or r.status_code == 422
    # make the balance bigger: second order
    o2 = (
        await buyer.post(
            f"{API}/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "shopa"}
        )
    ).json()
    await pay_order(env, buyer, o2)
    await staff.post(f"{API}/admin/orders/{o2['id']}/deliver", json={"text": "ok"})
    await buyer.post(f"{API}/orders/{o2['id']}/confirm")
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 900})
    assert r.json()["error"]["code"] == "insufficient_balance"
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 800})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 500})
    assert r.json()["error"]["code"] == "payout_open"
    assert (await rb.get(f"{API}/reseller/me")).json()["balances"]["available_minor"] == 0
    assert (
        await buyer.post(f"{API}/admin/payouts/{pid}/decide", json={"action": "paid"})
    ).status_code == 403
    r = await admin.post(
        f"{API}/admin/payouts/{pid}/decide", json={"action": "paid", "note": "tx 1"}
    )
    assert r.status_code == 200 and r.json()["status"] == "paid"
    rid = await env.scalar("SELECT id FROM resellers")
    # earned -800 (two payments) then +800 paid out
    assert await ledger_sum(env, f"reseller_payable:{rid}") == 0
    assert await ledger_sum(env, "cash:payouts") == -800
    await assert_ledger_balanced(env)
    assert (
        await admin.post(f"{API}/admin/payouts/{pid}/decide", json={"action": "rejected"})
    ).status_code == 409
    assert (await rb.get(f"{API}/reseller/me")).json()["balances"]["paid_minor"] == 800
    assert (await admin.get(f"{API}/admin/payouts", params={"status": "paid"})).json()["total"] == 1


async def test_payout_rejected_frees_balance_and_cross_reseller_isolation(env):
    await set_settings(
        env, {**FEATURES, "resellers": {"settle_after_hours": 0, "min_payout_minor": 100}}
    )
    rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=400)
    ob, ou = await env.login("buyer")
    await make_reseller(env, ou, "shopb")
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    o = (
        await buyer.post(
            f"{API}/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "shopa"}
        )
    ).json()
    await pay_order(env, buyer, o)
    await staff.post(f"{API}/admin/orders/{o['id']}/deliver", json={"text": "ok"})
    await buyer.post(f"{API}/orders/{o['id']}/confirm")
    await rb.patch(f"{API}/reseller/me", json={"payout_account": {"name": "A", "account": "x"}})
    # shopb cannot request against shopa's balance
    await ob.patch(f"{API}/reseller/me", json={"payout_account": {"name": "B", "account": "y"}})
    assert (await ob.post(f"{API}/reseller/payouts", json={"amount_minor": 100})).json()["error"][
        "code"
    ] == "insufficient_balance"
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 400})
    pid = r.json()["id"]
    await admin.post(
        f"{API}/admin/payouts/{pid}/decide", json={"action": "rejected", "note": "bad acct"}
    )
    assert (await rb.get(f"{API}/reseller/me")).json()["balances"]["available_minor"] == 400
    assert await ledger_sum(env, "cash:payouts") == 0
    # buyers / non-resellers have no center
    assert (await buyer.get(f"{API}/reseller/me")).json() is None
    assert (await buyer.get(f"{API}/reseller/orders")).status_code == 404
    assert (await ob.get(f"{API}/reseller/orders")).json()["total"] == 0
