"""Card management: batches, expiry, find, void, replace, DB trigger."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.cardvault import first_field
from app.modules.jobs import void_expired_cards
from tests.helpers import buy, delivered_order

ACCTS = [f"Acct{i}@Gmail.com----pw{i}----2fa{i}----US" for i in range(4)]


def future(days=30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


async def multi_product(env):
    p = await env.product(mode="auto_card", title="Gmail acct")
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE products SET delivery_format='Account----Password----2FA----Notes'")
        )
        await db.commit()
    return p


async def do_import(admin, product, lines, **extra):
    r = await admin.post(
        f"/api/v1/admin/products/{product.id}/cards/import",
        json={"lines": "\n".join(lines), **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_import_creates_batch_with_note_and_expiry(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    exp = future(10)
    res = await do_import(admin, p, ACCTS[:3], note="Nov batch", expires_at=exp)
    assert res["imported"] == 3 and res["batch_id"]
    r = await admin.get(f"/api/v1/admin/products/{p.id}/batches")
    (b,) = r.json()["items"]
    assert b["id"] == res["batch_id"] and b["note"] == "Nov batch" and b["count"] == 3
    assert b["available"] == 3 and b["sold"] == 0 and b["void"] == 0
    assert b["expires_at"].startswith(exp[:7])  # valid through the whole day
    d = datetime.fromisoformat(b["expires_at"].replace("Z", "+00:00"))
    assert d.hour == 0 and d.date() == (datetime.fromisoformat(exp).date() + timedelta(days=1))
    # nothing imported -> no batch
    res2 = await do_import(admin, p, ACCTS[:3])
    assert res2["imported"] == 0 and res2["duplicates"] == 3 and res2["batch_id"] is None
    # past expiry rejected
    r = await admin.post(
        f"/api/v1/admin/products/{p.id}/cards/import",
        json={"lines": "x", "expires_at": "2001-01-01"},
    )
    assert r.status_code == 422


async def test_per_line_expiry_suffix_is_stripped_before_sealing(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    d = future(5)
    res = await do_import(
        admin, p, [f"CODE-A----x|exp={d}", "CODE-B----y|exp=2999-99-99", "CODE-C"]
    )
    assert res["imported"] == 2 and res["rejected"] == 1  # invalid date rejected
    sealer = env.res.sealer
    tid = str(env.tenant_id)
    fp = sealer.fingerprint(tid, "CODE-A----x")  # fingerprint of the code WITHOUT the suffix
    assert await env.scalar("SELECT count(*) FROM card_stock WHERE fingerprint=:f", f=fp) == 1
    exp = await env.scalar("SELECT expires_at FROM card_stock WHERE fingerprint=:f", f=fp)
    assert exp.date() == datetime.fromisoformat(d).date() + timedelta(days=1)
    assert (
        await env.scalar(
            "SELECT expires_at FROM card_stock WHERE lookup_fp IS NOT NULL AND expires_at IS NULL"
        )
        is None
    )
    # same code re-imported without the suffix is a duplicate
    res = await do_import(admin, p, ["CODE-A----x"])
    assert res["duplicates"] == 1


async def test_find_by_content_and_first_field(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    res = await do_import(admin, p, ACCTS)
    r = await admin.post(
        "/api/v1/admin/cards/find",
        json={"lines": [ACCTS[1], "nope----nope", ACCTS[3] + "|exp=2030-01-01"], "by": "content"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 2 and body["missing"] == 1
    item = next(i for i in body["items"] if i["line"] == 1)
    assert item["status"] == "available" and item["batch_id"] == res["batch_id"]
    assert item["masked"].startswith("••••-") and ACCTS[1] not in str(body)
    assert item["product_id"] == str(p.id) and item["order_no"] is None
    # by first field, case-insensitive
    r = await admin.post(
        "/api/v1/admin/cards/find", json={"lines": ["acct2@GMAIL.com"], "by": "first_field"}
    )
    (hit,) = r.json()["items"]
    assert (
        hit["id"]
        == (await admin.get(f"/api/v1/admin/products/{p.id}/cards")).json()["items"][
            [
                c["id"]
                for c in (await admin.get(f"/api/v1/admin/products/{p.id}/cards")).json()["items"]
            ].index(hit["id"])
        ]["id"]
    )
    # limits and validation
    assert (
        await admin.post("/api/v1/admin/cards/find", json={"lines": ["x"] * 101, "by": "content"})
    ).status_code == 422
    assert (
        await admin.post("/api/v1/admin/cards/find", json={"lines": [], "by": "content"})
    ).status_code == 422
    assert (
        await admin.post("/api/v1/admin/cards/find", json={"lines": ["x"], "by": "regex"})
    ).status_code == 422
    # agents (non-admin) may not use it
    agent, _ = await env.login("agent")
    assert (
        await agent.post("/api/v1/admin/cards/find", json={"lines": ["x"], "by": "content"})
    ).status_code == 403


async def test_find_shows_order_no_for_sold_cards_and_is_tenant_scoped(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    await do_import(admin, p, ACCTS[:1])
    buyer, _ = await env.login("buyer")
    order = await delivered_order(env, buyer, p)
    r = await admin.post("/api/v1/admin/cards/find", json={"lines": [ACCTS[0]], "by": "content"})
    (item,) = r.json()["items"]
    assert item["status"] == "sold" and item["order_no"] == order["order_no"]
    # same code in another tenant is invisible (fingerprints are tenant-keyed + RLS)
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    other_product = await env.product(tenant_id=env.other_tenant_id)
    await env.import_codes(other_product, ["OTHER-TENANT-CODE"], tenant_id=env.other_tenant_id)
    r = await admin.post(
        "/api/v1/admin/cards/find", json={"lines": ["OTHER-TENANT-CODE"], "by": "content"}
    )
    assert r.json()["items"] == [] and r.json()["missing"] == 1
    _ = other_admin


async def test_void_by_ids_batch_and_state_rules(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    res = await do_import(admin, p, ACCTS)
    cards = (await admin.get(f"/api/v1/admin/products/{p.id}/cards")).json()["items"]
    ids = [c["id"] for c in cards[:2]]
    r = await admin.post("/api/v1/admin/cards/void", json={"ids": ids, "reason": "leaked"})
    assert r.status_code == 200 and r.json() == {"voided": 2}
    r = await admin.post("/api/v1/admin/cards/void", json={"ids": ids, "reason": "leaked"})
    assert r.json() == {"voided": 0}  # already void: nothing to do
    r = await admin.post(
        "/api/v1/admin/cards/void", json={"batch_id": res["batch_id"], "reason": "bad batch"}
    )
    assert r.json() == {"voided": 2}
    listed = (
        await admin.get(f"/api/v1/admin/products/{p.id}/cards", params={"status": "void"})
    ).json()
    assert listed["total"] == 4 and {c["void_reason"] for c in listed["items"]} == {
        "leaked",
        "bad batch",
    }
    assert (await admin.post("/api/v1/admin/cards/void", json={"reason": "x"})).status_code == 422
    assert (
        await admin.post(
            "/api/v1/admin/cards/void",
            json={"ids": ids, "batch_id": res["batch_id"], "reason": "x"},
        )
    ).status_code == 422
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='cards.void'") == 3
    # sold cards are never voided here
    await do_import(admin, p, ["SOLD-1----a"])
    buyer, _ = await env.login("buyer")
    await delivered_order(env, buyer, p)
    r = await admin.post(
        "/api/v1/admin/cards/void",
        json={
            "ids": [
                c["id"]
                for c in (
                    await admin.get(
                        f"/api/v1/admin/products/{p.id}/cards", params={"status": "sold"}
                    )
                ).json()["items"]
            ],
            "reason": "x",
        },
    )
    assert r.json() == {"voided": 0}


async def test_void_cross_tenant_ids_are_ignored(env):
    admin, _ = await env.login("admin")
    other_product = await env.product(tenant_id=env.other_tenant_id)
    await env.import_codes(other_product, ["OTHER-1"], tenant_id=env.other_tenant_id)
    async with env.owner_session(env.other_tenant_id) as db:
        foreign = await db.scalar(text("SELECT id FROM card_stock"))
    r = await admin.post("/api/v1/admin/cards/void", json={"ids": [str(foreign)], "reason": "x"})
    assert r.json() == {"voided": 0}
    async with env.owner_session(env.other_tenant_id) as db:
        status = await db.scalar(text("SELECT status FROM card_stock WHERE id=:i"), {"i": foreign})
    assert status == "available"
    r = await admin.get(f"/api/v1/admin/products/{other_product.id}/batches")
    assert r.status_code == 404
    r = await admin.post(f"/api/v1/admin/cards/{foreign}/replace", json={"code": "NEW"})
    assert r.status_code == 404


async def test_replace_card_voids_old_and_links(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    await do_import(admin, p, ACCTS[:2], note="b1", expires_at=future(20))
    cards = (await admin.get(f"/api/v1/admin/products/{p.id}/cards")).json()["items"]
    old = cards[0]
    r = await admin.post(
        f"/api/v1/admin/cards/{old['id']}/replace", json={"code": "FIXED----pw----2fa"}
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != old["id"] and new["status"] == "available"
    assert new["batch_id"] == old["batch_id"] and new["expires_at"] == old["expires_at"]
    assert await env.scalar("SELECT status FROM card_stock WHERE id=:i", i=old["id"]) == "void"
    assert (
        await env.scalar("SELECT void_reason FROM card_stock WHERE id=:i", i=old["id"])
        == "replaced"
    )
    assert (
        str(await env.scalar("SELECT replaced_by FROM card_stock WHERE id=:i", i=old["id"]))
        == new["id"]
    )
    # replaced card cannot be replaced again; duplicates refused
    assert (
        await admin.post(f"/api/v1/admin/cards/{old['id']}/replace", json={"code": "Z"})
    ).status_code == 409
    assert (
        await admin.post(f"/api/v1/admin/cards/{new['id']}/replace", json={"code": ACCTS[1]})
    ).status_code == 409
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='cards.replace'") == 1
    # lookup_fp follows the first field of the replacement
    r = await admin.post("/api/v1/admin/cards/find", json={"lines": ["fixed"], "by": "first_field"})
    assert [i["id"] for i in r.json()["items"]] == [new["id"]]


async def test_list_filters(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    r1 = await do_import(admin, p, ACCTS[:2], expires_at=future(3))
    r2 = await do_import(admin, p, ACCTS[2:])
    base = f"/api/v1/admin/products/{p.id}/cards"
    assert (await admin.get(base, params={"batch_id": r1["batch_id"]})).json()["total"] == 2
    assert (await admin.get(base, params={"batch_id": r2["batch_id"]})).json()["total"] == 2
    assert (await admin.get(base, params={"expiring_days": 7})).json()["total"] == 2
    assert (await admin.get(base, params={"expiring_days": 1})).json()["total"] == 0
    buyer, _ = await env.login("buyer")
    order = await delivered_order(env, buyer, p)
    got = (await admin.get(base, params={"order_no": order["order_no"]})).json()
    assert got["total"] == 1 and got["items"][0]["order_no"] == order["order_no"]
    assert (await admin.get(base, params={"status": "sold"})).json()["total"] == 1
    b = (await admin.get(f"/api/v1/admin/products/{p.id}/batches")).json()["items"]
    assert sum(x["sold"] for x in b) == 1 and sum(x["available"] for x in b) == 3


async def test_delivery_skips_expired_cards_and_stock_excludes_them(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    await do_import(admin, p, ["OLD-1----a", "OLD-2----b", "GOOD-1----c"])
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE card_stock SET expires_at = now() - interval '1 hour' "
                "WHERE fingerprint = ANY(:f)"
            ),
            {
                "f": [
                    env.res.sealer.fingerprint(str(env.tenant_id), c)
                    for c in ("OLD-1----a", "OLD-2----b")
                ]
            },
        )
        await db.commit()
    detail = (await env.client().get(f"/api/v1/products/{p.id}")).json()
    assert detail["stock"] == 1  # expired cards no longer count
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(p.id), "qty": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    order = await delivered_order(env, buyer, p)
    rev = (await buyer.post(f"/api/v1/orders/{order['id']}/reveal")).json()
    assert rev["secrets"] == ["GOOD-1----c"]


async def test_delivery_claim_skips_expired_between_order_and_payment(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    await do_import(admin, p, ["ONLY-1----a"])
    buyer, _ = await env.login("buyer")
    from tests.helpers import pay_order

    order = await buy(buyer, p)
    async with env.owner_session() as db:
        await db.execute(text("UPDATE card_stock SET expires_at = now() - interval '1 second'"))
        await db.commit()
    await pay_order(env, buyer, order)
    await env.drain()
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "manual_review"  # nothing deliverable: an expired card is never sold


async def test_hourly_job_voids_expired_available_cards(env):
    admin, _ = await env.login("admin")
    p = await multi_product(env)
    await do_import(admin, p, ["E-1----a", "E-2----b"])
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE card_stock SET expires_at = now() - interval '1 hour' "
                "WHERE id = (SELECT id FROM card_stock ORDER BY id LIMIT 1)"
            )
        )
        await db.commit()
    assert await void_expired_cards(env.res.maker) == 1
    assert await void_expired_cards(env.res.maker) == 0
    assert await env.scalar("SELECT void_reason FROM card_stock WHERE status='void'") == "expired"
    assert await env.scalar("SELECT voided_at IS NOT NULL FROM card_stock WHERE status='void'")


# ---------------------------------------------------------------- DB trigger (raw SQL as app_api)
async def test_trigger_app_api_can_only_void(env):
    p = await env.product()
    await env.import_codes(p, ["T-1", "T-2"])
    ids = [r for r in await _ids(env)]
    async with env.api_session() as db:
        await db.execute(
            text("UPDATE card_stock SET status='void', void_reason='x' WHERE id=:i"), {"i": ids[0]}
        )
        await db.commit()
    for sql in (
        "UPDATE card_stock SET status='available' WHERE id=:i",  # void -> available
        "UPDATE card_stock SET status='sold' WHERE id=:i",
        "UPDATE card_stock SET status='void', void_reason='again' WHERE id=:i",  # void -> void
    ):
        async with env.api_session() as db:
            with pytest.raises(DBAPIError, match="may only void"):
                await db.execute(text(sql), {"i": ids[0]})
            await db.rollback()
    # an available card cannot be sold / re-owned by app_api either
    async with env.api_session() as db:
        with pytest.raises(DBAPIError, match="may only void"):
            await db.execute(text("UPDATE card_stock SET status='sold' WHERE id=:i"), {"i": ids[1]})
        await db.rollback()
    for sql in (
        "UPDATE card_stock SET order_id = NULL WHERE id=:i",
        "UPDATE card_stock SET blob = '\\x00' WHERE id=:i",
        "UPDATE card_stock SET fingerprint = 'x' WHERE id=:i",
        "UPDATE card_stock SET expires_at = now() WHERE id=:i",
    ):
        async with env.api_session() as db:
            with pytest.raises(DBAPIError, match="permission denied"):
                await db.execute(text(sql), {"i": ids[1]})
            await db.rollback()
    async with env.api_session() as db:  # blob stays unreadable
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT blob FROM card_stock"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("DELETE FROM card_stock"))
        await db.rollback()


async def test_trigger_does_not_restrict_delivery_role(env):
    from app.core.db import tenant_session

    p = await env.product()
    await env.import_codes(p, ["D-1"])
    cid = (await _ids(env))[0]
    async with tenant_session(env.delivery_ctx.maker, env.tenant_id) as db:
        await db.execute(
            text("UPDATE card_stock SET status='void', void_reason='defective' WHERE id=:i"),
            {"i": cid},
        )
        await db.commit()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE card_stock SET fingerprint='x' WHERE id=:i"), {"i": cid})
        await db.rollback()


async def _ids(env) -> list[uuid.UUID]:
    async with env.owner_session() as db:
        return [
            r[0]
            for r in (
                await db.execute(text("SELECT id FROM card_stock ORDER BY created_at, id"))
            ).all()
        ]


def test_first_field_helper():
    assert first_field("Acct----pw----2fa", "----") == "Acct"
    assert first_field("a|b|c", "|") == "a"
    assert first_field("whole code", "----") == "whole code"
    assert first_field("  x  ", ",") == "x"
