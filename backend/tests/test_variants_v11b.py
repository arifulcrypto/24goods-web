"""review-v11 W1 follow-ups: variant ``sold`` counter, moving product-level cards into a variant."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.helpers import pay_order
from tests.test_variants import CODES, add_variant, order_req

API = "/api/v1"


async def test_paid_variant_order_counts_product_and_variant_sold(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00")
    b = await add_variant(admin, p, "B", "6.00")
    buyer, _ = await env.login("buyer")
    o = (await order_req(buyer, p, a, qty=2)).json()
    await pay_order(env, buyer, o)
    assert await env.scalar("SELECT sold FROM products WHERE id=:p", p=p.id) == 2
    assert await env.scalar("SELECT sold FROM product_variants WHERE id=:v", v=a["id"]) == 2
    assert await env.scalar("SELECT sold FROM product_variants WHERE id=:v", v=b["id"]) == 0


async def test_product_level_cards_move_into_a_variant(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    r = await admin.post(
        f"{API}/admin/products/{p.id}/cards/import", json={"lines": "\n".join(CODES[:3])}
    )
    assert r.status_code == 200 and r.json()["imported"] == 3  # before any variant existed
    a = await add_variant(admin, p, "A", "5.00")
    buyer, _ = await env.login("buyer")
    r = await order_req(buyer, p, a)
    assert r.status_code == 409  # product-level cards are not sold through a variant
    url = f"{API}/admin/products/{p.id}/cards/assign-variant"
    agent, _ = await env.login("agent")
    assert (await agent.post(url, json={"variant_id": a["id"]})).status_code == 403
    r = await admin.post(url, json={"variant_id": a["id"], "count": 2})
    assert r.status_code == 200 and r.json() == {"moved": 2}, r.text
    r = await admin.post(url, json={"variant_id": a["id"]})
    assert r.json() == {"moved": 1}
    assert await env.scalar(
        "SELECT count(*) FROM card_stock WHERE variant_id=:v AND status='available'", v=a["id"]
    ) == 3  # fmt: skip
    assert await env.scalar(
        "SELECT count(*) FROM audit_log WHERE action='cards.assigned_to_variant'"
    ) == 2  # fmt: skip
    o = (await order_req(buyer, p, a)).json()
    await pay_order(env, buyer, o)
    assert await env.drain() >= 1
    assert (await buyer.get(f"{API}/orders/{o['id']}")).json()["status"] == "delivered"


async def test_card_guard_allows_only_the_variant_move(env):
    """The database guard still refuses everything else app_api might try on card_stock."""
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    other = await env.product(mode="auto_card", price=1990, floor=100, title="Other")
    await admin.post(f"{API}/admin/products/{p.id}/cards/import", json={"lines": CODES[0]})
    a = await add_variant(admin, p, "A", "5.00")
    foreign = await add_variant(admin, other, "X", "5.00")
    cid = await env.scalar("SELECT id FROM card_stock WHERE product_id=:p", p=p.id)
    async with env.api_session() as db:
        with pytest.raises(DBAPIError):  # a variant of ANOTHER product
            await db.execute(
                text("UPDATE card_stock SET variant_id=:v WHERE id=:c"),
                {"v": foreign["id"], "c": cid},
            )
        await db.rollback()
        with pytest.raises(DBAPIError):  # anything besides variant_id changes too
            await db.execute(
                text("UPDATE card_stock SET variant_id=:v, status='sold' WHERE id=:c"),
                {"v": a["id"], "c": cid},
            )
        await db.rollback()
    async with env.api_session() as db:  # the allowed move, then no second move
        await db.execute(
            text("UPDATE card_stock SET variant_id=:v WHERE id=:c"), {"v": a["id"], "c": cid}
        )
        await db.commit()
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE card_stock SET variant_id=NULL WHERE id=:c"), {"c": cid})
        await db.rollback()


async def test_assign_refuses_archived_or_foreign_variant(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    other = await env.product(mode="auto_card", price=1990, floor=100, title="Other")
    await admin.post(f"{API}/admin/products/{p.id}/cards/import", json={"lines": CODES[0]})
    a = await add_variant(admin, p, "A", "5.00")
    x = await add_variant(admin, other, "X", "5.00")
    url = f"{API}/admin/products/{p.id}/cards/assign-variant"
    assert (await admin.post(url, json={"variant_id": x["id"]})).status_code in (404, 422)
    assert (await admin.delete(f"{API}/admin/products/{p.id}/variants/{a['id']}")).status_code in (
        200,
        204,
    )
    assert (await admin.post(url, json={"variant_id": a["id"]})).status_code in (404, 422)


async def test_variantless_order_never_takes_a_variants_cards(env):
    """Audit M1: a pending variant-less order keeps its product-level cards; assign-variant
    does not move them, and a variant order cannot be promised them."""
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card", price=1990, floor=100)
    await admin.post(
        f"{API}/admin/products/{p.id}/cards/import", json={"lines": "\n".join(CODES[:3])}
    )
    buyer, _ = await env.login("buyer")
    legacy = (await order_req(buyer, p, qty=2)).json()  # before variants existed
    a = await add_variant(admin, p, "A", "5.00")
    r = await admin.post(
        f"{API}/admin/products/{p.id}/cards/assign-variant", json={"variant_id": a["id"]}
    )
    assert r.json() == {"moved": 1}  # 3 product-level cards, 2 promised to the legacy order
    other, _ = await env.login("buyer")
    assert (await order_req(other, p, a, qty=2)).status_code == 409  # only 1 card in A
    va = (await order_req(other, p, a)).json()
    await pay_order(env, buyer, legacy)
    await pay_order(env, other, va)
    assert await env.drain() >= 2
    assert (await buyer.get(f"{API}/orders/{legacy['id']}")).json()["status"] == "delivered"
    assert (await other.get(f"{API}/orders/{va['id']}")).json()["status"] == "delivered"
    # each order got cards from its own pool
    assert await env.scalar(
        "SELECT count(*) FROM card_stock WHERE order_id=:o AND variant_id IS NULL", o=legacy["id"]
    ) == 2  # fmt: skip
    assert await env.scalar(
        "SELECT count(*) FROM card_stock WHERE order_id=:o AND variant_id=:v",
        o=va["id"],
        v=a["id"],
    ) == 1  # fmt: skip
