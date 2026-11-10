"""review-v11 W1: product variants (SKUs) — price, stock, terms, cart, tenant isolation."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import func, select, text

from app.core.errors import NotFound
from app.models import ProductVariant
from app.modules.admin import variants as variant_admin
from app.modules.admin.schemas import VariantPatch
from app.modules.catalog import variants
from app.modules.orders.service import expire_pending
from tests.helpers import pay_order
from tests.test_commerce_v4 import checkout, line, set_fee, set_settings
from tests.test_commerce_v5 import FEATURES, make_reseller

API = "/api/v1"
CODES = [f"VAR{i}----pw{i}----2fa{i}----n" for i in range(10)]


async def add_variant(admin, product, name="A", price="5.00", floor="1.00", **extra):
    r = await admin.post(
        f"{API}/admin/products/{product.id}/variants",
        json={"name_i18n": {"en": name, "zh-CN": f"{name}-zh"}, "price": price,
              "floor_price": floor, **extra},
    )  # fmt: skip
    assert r.status_code == 200, r.text
    return r.json()


def order_req(client, product, variant=None, qty=1, **extra):
    body = {"product_id": str(product.id), "qty": qty, **extra}
    if variant is not None:
        body["variant_id"] = variant["id"] if isinstance(variant, dict) else str(variant)
    return client.post(f"{API}/orders", json=body)


async def vstock(env, vid):
    return await env.scalar("SELECT manual_stock FROM product_variants WHERE id=:i", i=vid)


async def test_price_comes_from_variant_and_client_amount_ignored(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "8.00")
    buyer, _ = await env.login("buyer")
    r = await order_req(buyer, p, a, qty=2, amount="0.01", price="0.01", amount_minor=1)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["amount"] == "10.00" and o["subtotal"] == "10.00"
    assert o["variant_id"] == a["id"] and o["variant_name"] == "A"
    r = await order_req(buyer, p, b)
    assert r.json()["amount"] == "8.00"
    # the buyer view and the admin order detail carry the variant name
    admin_view = (await admin.get(f"{API}/admin/orders/{o['id']}")).json()
    assert admin_view["variant_name"] == "A"
    # price change on the variant is server-side and audited with before/after
    r = await admin.patch(f"{API}/admin/products/{p.id}/variants/{a['id']}", json={"price": "6.00"})
    assert r.status_code == 200 and r.json()["price"] == "6.00"
    assert (await order_req(buyer, p, a)).json()["amount"] == "6.00"
    row = await env.scalar(
        "SELECT (before->>'price_minor') || '>' || (after->>'price_minor') FROM audit_log "
        "WHERE action='variant.updated'"
    )
    assert row == "500>600"


async def test_variant_required_unknown_and_product_without_variants_unchanged(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    plain = await env.product(mode="manual", price=700, floor=100, title="Plain")
    a = await add_variant(admin, p, "A", "5.00")
    buyer, _ = await env.login("buyer")
    r = await order_req(buyer, p)  # variants exist: one is required
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_required"
    r = await order_req(buyer, p, "00000000-0000-0000-0000-000000000001")
    assert r.status_code == 404
    r = await order_req(buyer, plain, a)  # a variant of ANOTHER product
    assert r.status_code == 404
    o = (await order_req(buyer, plain)).json()  # unchanged behaviour
    assert o["amount"] == "7.00" and o["variant_id"] is None and o["variant_name"] is None
    # archiving the last variant makes the product behave as before
    r = await admin.patch(
        f"{API}/admin/products/{p.id}/variants/{a['id']}", json={"status": "archived"}
    )
    assert r.status_code == 200
    assert (await order_req(buyer, p)).json()["amount"] == "19.90"


async def test_archived_variant_refused_and_delete_archives_when_used(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "6.00")
    buyer, _ = await env.login("buyer")
    assert (await order_req(buyer, p, a)).status_code == 200
    r = await admin.delete(f"{API}/admin/products/{p.id}/variants/{a['id']}")
    assert r.json() == {"outcome": "archived"}  # has an order: archive, never delete
    r = await order_req(buyer, p, a)
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_archived"
    r = await admin.delete(f"{API}/admin/products/{p.id}/variants/{b['id']}")
    assert r.json() == {"outcome": "deleted"}  # unreferenced: removed
    assert await env.scalar("SELECT count(*) FROM product_variants") == 1
    actions = {"variant.created", "variant.archived", "variant.deleted"}
    got = await env.scalar(
        "SELECT count(DISTINCT action) FROM audit_log WHERE action = ANY(:a)", a=list(actions)
    )
    assert got == 3


async def test_manual_stock_per_variant_and_release_on_expiry_and_cancel(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00", manual_stock=2)
    b = await add_variant(admin, p, "B", "6.00", manual_stock=1)
    buyer, _ = await env.login("buyer")
    o1 = (await order_req(buyer, p, a, qty=2)).json()
    assert await vstock(env, a["id"]) == 0 and await vstock(env, b["id"]) == 1
    # the product-level stock was never touched
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:i", i=str(p.id)) is None
    r = await order_req(buyer, p, a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    assert (await order_req(buyer, p, b)).status_code == 200  # B is independent of A
    # cancel returns exactly the variant's reservation (once)
    r = await buyer.post(f"{API}/orders/{o1['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert await vstock(env, a["id"]) == 2
    # expiry returns the other order's reservation
    o3 = (await order_req(buyer, p, a, qty=2)).json()
    assert await vstock(env, a["id"]) == 0
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET expires_at = now() - interval '1 second' WHERE id=:i"),
            {"i": o3["id"]},
        )
        await db.commit()
    assert await expire_pending(env.res.maker, env.res) == 1
    assert await vstock(env, a["id"]) == 2
    assert await expire_pending(env.res.maker, env.res) == 0
    assert await vstock(env, a["id"]) == 2
    assert await vstock(env, b["id"]) == 0  # b's order is still pending


async def test_concurrent_variant_orders_do_not_oversell(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00", manual_stock=2)
    clients = [(await env.login("buyer"))[0] for _ in range(5)]
    rs = await asyncio.gather(*[order_req(c, p, a) for c in clients])
    assert len([r for r in rs if r.status_code == 200]) == 2, [r.status_code for r in rs]
    assert await vstock(env, a["id"]) == 0


async def test_auto_card_stock_is_per_variant(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "6.00")
    # import requires a variant once the product has some; foreign / unknown ids are 404
    r = await admin.post(f"{API}/admin/products/{p.id}/cards/import", json={"lines": CODES[0]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_required"
    r = await admin.post(
        f"{API}/admin/products/{p.id}/cards/import",
        json={"lines": CODES[0], "variant_id": "00000000-0000-0000-0000-000000000009"},
    )
    assert r.status_code == 404
    r = await admin.post(
        f"{API}/admin/products/{p.id}/cards/import",
        json={"lines": "\n".join(CODES[:2]), "variant_id": a["id"]},
    )
    assert r.status_code == 200 and r.json()["imported"] == 2
    # list filter + masked rows carry the variant
    r = await admin.get(f"{API}/admin/products/{p.id}/cards", params={"variant_id": a["id"]})
    assert r.json()["total"] == 2 and r.json()["items"][0]["variant_id"] == a["id"]
    assert (
        await admin.get(f"{API}/admin/products/{p.id}/cards", params={"variant_id": b["id"]})
    ).json()["total"] == 0
    pub = (await env.client().get(f"{API}/products/{p.id}")).json()
    stocks = {v["name"]: v["stock"] for v in pub["variants"]}
    assert stocks == {"A": 2, "B": 0} and pub["stock"] == 2
    buyer, _ = await env.login("buyer")
    # A's cards cannot be bought through B
    r = await order_req(buyer, p, b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    o1 = (await order_req(buyer, p, a, qty=2)).json()
    r = await order_req(buyer, p, a)  # both A cards are now promised
    assert r.status_code == 409
    await pay_order(env, buyer, o1)
    assert await env.drain() >= 1
    assert (await buyer.get(f"{API}/orders/{o1['id']}")).json()["status"] == "delivered"
    sold = await env.scalar(
        "SELECT count(*) FROM card_stock WHERE status='sold' AND variant_id=:v", v=a["id"]
    )
    assert sold == 2


async def test_last_card_of_a_not_sold_through_b_in_worker(env):
    """Even if the buy-time check passed, the worker claims by variant: B's order with only
    A's card left ends in manual_review, A's card stays available."""
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "6.00")
    await admin.post(
        f"{API}/admin/products/{p.id}/cards/import", json={"lines": CODES[0], "variant_id": b["id"]}
    )
    buyer, _ = await env.login("buyer")
    ob = (await order_req(buyer, p, b)).json()
    await pay_order(env, buyer, ob)
    async with env.owner_session() as db:  # A's card appears, B's card is voided before delivery
        await db.execute(
            text("UPDATE card_stock SET status='void', void_reason='t' WHERE variant_id=:v"),
            {"v": b["id"]},
        )
        await db.commit()
    await admin.post(
        f"{API}/admin/products/{p.id}/cards/import", json={"lines": CODES[1], "variant_id": a["id"]}
    )
    await env.drain()
    assert (await buyer.get(f"{API}/orders/{ob['id']}")).json()["status"] == "manual_review"
    assert (
        await env.scalar(
            "SELECT count(*) FROM card_stock WHERE status='available' AND variant_id=:v", v=a["id"]
        )
        == 1
    )


async def test_terms_inheritance_snapshot(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    await admin.patch(f"{API}/admin/products/{p.id}", json={"after_sales_hours": 48})
    inherit = await add_variant(admin, p, "Inherit", "5.00")
    own = await add_variant(admin, p, "Own", "6.00", after_sales_months=3)
    sub = await add_variant(
        admin, p, "Sub", "7.00", subscription={"period_policy": "fixed_days", "period_days": 30}
    )
    buyer, _ = await env.login("buyer")
    terms = {}
    for name, v in (("inherit", inherit), ("own", own), ("sub", sub)):
        o = (await order_req(buyer, p, v)).json()
        terms[name] = await env.scalar("SELECT terms::text FROM orders WHERE id=:i", i=o["id"])
    t = {k: json.loads(v) for k, v in terms.items()}
    assert t["inherit"] == {
        "after_sales_hours": 48,
        "after_sales_months": None,
        "subscription": None,
    }
    assert t["own"]["after_sales_months"] == 3 and t["own"]["after_sales_hours"] is None
    assert t["sub"]["after_sales_hours"] == 48 and t["sub"]["subscription"]["period_days"] == 30
    pub = (await env.client().get(f"{API}/products/{p.id}")).json()
    by = {v["name"]: v for v in pub["variants"]}
    assert by["Own"]["after_sales_months"] == 3 and by["Sub"]["subscription"] is True
    assert by["Inherit"]["after_sales_hours"] == 48 and by["Inherit"]["subscription"] is False


async def test_cart_and_checkout_with_two_variants_of_one_product(env):
    await set_fee(env, 30)
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00", manual_stock=3)
    b = await add_variant(admin, p, "B", "8.00", manual_stock=3)
    buyer, _ = await env.login("buyer")
    r = await buyer.post(
        f"{API}/cart/quote",
        json={"items": [line(p, 1, variant_id=a["id"]), line(p, 2, variant_id=b["id"]), line(p)]},
    )
    q = r.json()
    assert [(ln["available"], ln["unit_price"], ln["variant_name"]) for ln in q["lines"][:2]] == [
        (True, "5.00", "A"),
        (True, "8.00", "B"),
    ]
    assert q["lines"][2]["unavailable_reason"] == "variant_required"
    assert q["subtotal"] == "21.00"  # 5 + 2*8; client amounts do not exist
    r = await checkout(buyer, [line(p, 1, variant_id=a["id"]), line(p, 2, variant_id=b["id"])])
    assert r.status_code == 200, r.text
    body = r.json()
    names = sorted((o["variant_name"], o["subtotal"]) for o in body["orders"])
    assert names == [("A", "5.00"), ("B", "16.00")]
    # fee 3% per line: ceil(5.00*3%)=0.15, ceil(16.00*3%)=0.48 -> 21.63
    assert body["checkout"]["amount"] == "21.63"
    assert await vstock(env, a["id"]) == 2 and await vstock(env, b["id"]) == 1
    # a line without a variant is a 422, an archived one too
    r = await checkout(buyer, [line(p)])
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_required"
    # cancel the checkout: both variants get their stock back
    r = await buyer.post(f"{API}/checkouts/{body['checkout']['checkout_no']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert await vstock(env, a["id"]) == 3 and await vstock(env, b["id"]) == 3
    await admin.patch(
        f"{API}/admin/products/{p.id}/variants/{b['id']}", json={"status": "archived"}
    )
    r = await checkout(buyer, [line(p, 1, variant_id=b["id"])])
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_archived"


async def test_checkout_auto_card_variants_do_not_oversell(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "6.00")
    for v, codes in ((a, CODES[:1]), (b, CODES[1:3])):
        await admin.post(
            f"{API}/admin/products/{p.id}/cards/import",
            json={"lines": "\n".join(codes), "variant_id": v["id"]},
        )
    buyer, _ = await env.login("buyer")
    # two A lines (1 + 1) exceed A's single card even though B has two
    r = await checkout(buyer, [line(p, 1, variant_id=a["id"]), line(p, 1, variant_id=a["id"])])
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    r = await checkout(buyer, [line(p, 1, variant_id=a["id"]), line(p, 2, variant_id=b["id"])])
    assert r.status_code == 200, r.text


async def test_storefront_markup_applies_on_variant_price(env):
    await set_fee(env, 0)
    await set_settings(env, FEATURES)
    admin, _ = await env.login("admin")
    _rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=300)
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    buyer, _ = await env.login("buyer")
    r = await buyer.post(
        f"{API}/orders",
        json={"product_id": str(p.id), "variant_id": a["id"], "qty": 2},
        headers={"X-Storefront": "shopa"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == "16.00"  # (5.00 + 3.00) * 2
    assert await env.scalar("SELECT reseller_markup_minor FROM orders") == 600
    pub = (
        await env.client().get(f"{API}/products/{p.id}", headers={"X-Storefront": "shopa"})
    ).json()
    assert pub["variants"][0]["price"] == "8.00" and pub["price"] == "8.00"


async def test_catalog_shows_from_price_and_hides_archived(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    await add_variant(admin, p, "Big", "9.00", sort=2)
    await add_variant(admin, p, "Small", "3.00", sort=1)
    arch = await add_variant(admin, p, "Old", "1.00", sort=0)
    await admin.patch(
        f"{API}/admin/products/{p.id}/variants/{arch['id']}", json={"status": "archived"}
    )
    pub = (await env.client().get(f"{API}/products/{p.id}")).json()
    assert pub["has_variants"] and pub["price"] == "3.00" and pub["price_from"] is True
    assert [v["name"] for v in pub["variants"]] == ["Small", "Big"]  # sorted, archived hidden
    lst = (await env.client().get(f"{API}/products")).json()
    item = next(i for i in lst["items"] if i["id"] == str(p.id))
    assert item["price"] == "3.00" and len(item["variants"]) == 2
    # admin sees every variant, including archived, with stock
    av = (await admin.get(f"{API}/admin/products/{p.id}")).json()["variants"]
    assert {v["status"] for v in av} == {"active", "archived"}


async def test_cross_tenant_variant_refused(env):
    admin, _ = await env.login("admin")
    other_p = await env.product(mode="manual", tenant_id=env.other_tenant_id)
    async with env.owner_session(env.other_tenant_id) as db:
        foreign = ProductVariant(
            tenant_id=env.other_tenant_id,
            product_id=other_p.id,
            name_i18n={"en": "X", "zh-CN": "X"},
            price_minor=100,
        )
        db.add(foreign)
        await db.commit()
    fid = str(foreign.id)
    p = await env.product(mode="manual", price=1990, floor=100)
    mine = await add_variant(admin, p, "A", "5.00")
    buyer, _ = await env.login("buyer")
    assert (await order_req(buyer, p, fid)).status_code == 404
    r = await checkout(buyer, [line(p, 1, variant_id=fid)])
    assert r.status_code == 404
    # another tenant's session can neither resolve, list, patch nor delete this tenant's variant
    tid2 = env.other_tenant_id
    async with env.api_session(tid2) as db:
        assert await db.scalar(select(func.count()).select_from(ProductVariant)) == 1  # RLS
        with pytest.raises(NotFound):
            await variants.resolve(db, tid2, p, uuid.UUID(mine["id"]))
        with pytest.raises(NotFound):
            await variant_admin.list_variants(db, tid2, p.id)
        other_admin = await env.user("admin", tenant_id=tid2)
        with pytest.raises(NotFound):
            await variant_admin.patch(
                db, p.id, uuid.UUID(mine["id"]), VariantPatch(price="0.01"), other_admin, None
            )
        with pytest.raises(NotFound):
            await variant_admin.delete(db, p.id, uuid.UUID(mine["id"]), other_admin, None)
    # card import with another tenant's variant on my auto-card product
    cp = await env.product(mode="auto_card")
    await add_variant(admin, cp, "C", "2.00")
    r = await admin.post(
        f"{API}/admin/products/{cp.id}/cards/import", json={"lines": CODES[0], "variant_id": fid}
    )
    assert r.status_code == 404
    assert (
        await env.scalar("SELECT price_minor FROM product_variants WHERE id=:i", i=mine["id"])
        == 500
    )


async def test_variant_floor_enforced_on_reprice(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "10.00", floor="8.00")
    buyer, _ = await env.login("buyer")
    o = (await order_req(buyer, p, a, qty=2)).json()
    r = await admin.patch(
        f"{API}/admin/orders/{o['id']}/price", json={"amount": "15.00", "reason": "deal"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "reprice_below_floor"  # floor 16
    r = await admin.patch(
        f"{API}/admin/orders/{o['id']}/price", json={"amount": "16.00", "reason": "deal"}
    )
    assert r.status_code == 200, r.text
    assert (await admin.get(f"{API}/admin/orders/{o['id']}")).json()["floor_price"] == "8.00"


async def test_variant_validation(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    url = f"{API}/admin/products/{p.id}/variants"
    r = await admin.post(
        url, json={"name_i18n": {"en": "A"}, "price": "5.00", "floor_price": "6.00"}
    )
    assert r.status_code == 422  # floor above price
    r = await admin.post(url, json={"name_i18n": {"zh-CN": "x"}, "price": "5.00"})
    assert r.status_code == 422  # en is the source locale
    r = await admin.post(
        url,
        json={
            "name_i18n": {"en": "A"},
            "price": "5.00",
            "after_sales_hours": 1,
            "after_sales_months": 1,
        },
    )
    assert r.status_code == 422
    buyer, _ = await env.login("buyer")
    r = await buyer.post(url, json={"name_i18n": {"en": "A"}, "price": "5.00"})
    assert r.status_code in (401, 403)
    r = await admin.post(url, json={"name_i18n": {"en": "A"}, "price": "0"})
    assert r.status_code == 422


async def test_promo_applies_to_variant_price(env):
    await set_settings(env, {"features": {"promo_codes": True}})
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    r = await admin.post(
        f"{API}/admin/promo-codes", json={"code": "SAVE10", "kind": "percent", "value": 100}
    )
    assert r.status_code == 200, r.text
    buyer, _ = await env.login("buyer")
    v = (
        await buyer.post(
            f"{API}/promo/validate",
            json={"code": "save10", "items": [{"product_id": str(p.id), "variant_id": a["id"]}]},
        )
    ).json()
    assert v["eligible_subtotal_minor"] == 500 and v["discount_minor"] == 50  # variant, not 19.90
    o = (await order_req(buyer, p, a, promo_code="save10")).json()
    assert o["subtotal"] == "5.00" and o["discount"] == "0.50"
