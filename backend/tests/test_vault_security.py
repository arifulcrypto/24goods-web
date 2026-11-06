"""Card secrets: admin-only import, dedup, sealing, DB column grants, key separation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

APP = Path(__file__).resolve().parent.parent / "app"


async def test_import_dedup_limits_and_masked_listing(env, caplog):
    admin, _ = await env.login("admin")
    p = await env.product()
    url = f"/api/v1/admin/products/{p.id}/cards/import"
    with caplog.at_level("DEBUG"):
        r = await admin.post(
            url,
            json={
                "lines": "CODE-AAA-1\nCODE-BBB-2\n\nCODE-AAA-1\n" + "x" * 600 + "\nbad\u0001code"
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.pop("batch_id")
    assert body == {"imported": 2, "duplicates": 1, "rejected": 2}
    r = await admin.post(url, json={"lines": "CODE-BBB-2\nCODE-CCC-3"})
    body = r.json()
    assert body.pop("batch_id")
    assert body == {"imported": 1, "duplicates": 1, "rejected": 0}
    assert "CODE-AAA-1" not in caplog.text
    r = await admin.post(url, json={"lines": "\n".join(f"C{i}" for i in range(5001))})
    assert r.status_code == 422
    listing = (await admin.get(f"/api/v1/admin/products/{p.id}/cards")).json()
    assert listing["total"] == 3
    for item in listing["items"]:
        assert set(item) == {  # CardRow: never a code, only metadata
            "id",
            "masked",
            "status",
            "batch_id",
            "expires_at",
            "void_reason",
            "order_id",
            "order_no",
            "created_at",
        }
        assert "CODE" not in item["masked"]
    blob = await env.scalar("SELECT blob FROM card_stock LIMIT 1")
    assert b"CODE" not in bytes(blob)
    audit = await env.scalar(
        "SELECT detail::text FROM audit_log WHERE action='cards.import' ORDER BY id LIMIT 1"
    )
    assert "CODE" not in audit and '"imported": 2' in audit
    manual = await env.product(mode="manual")
    r = await admin.post(f"/api/v1/admin/products/{manual.id}/cards/import", json={"lines": "X"})
    assert r.status_code == 422


async def test_app_api_cannot_select_blob(env):
    p = await env.product()
    await env.import_codes(p, ["SECRET-1"])
    async with env.api_session() as db:
        n = await db.scalar(text("SELECT count(*) FROM card_stock"))
        assert n == 1
        ok = await db.scalar(text("SELECT fingerprint FROM card_stock LIMIT 1"))
        assert ok
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT blob FROM card_stock"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("SELECT * FROM card_stock"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE card_stock SET blob = '\\x00'"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE card_stock SET order_id = NULL"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("DELETE FROM audit_log"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE ledger_entries SET amount_minor = 1"))


async def test_app_delivery_can_read_blob(env):
    from app.core.db import tenant_session

    p = await env.product()
    await env.import_codes(p, ["SECRET-2"])
    async with tenant_session(env.delivery_ctx.maker, env.tenant_id) as db:
        blob = await db.scalar(text("SELECT blob FROM card_stock LIMIT 1"))
        assert blob and len(bytes(blob)) > 100
        with pytest.raises(DBAPIError, match="permission denied"):
            await db.execute(text("UPDATE card_stock SET blob = '\\x00'"))


def test_api_code_never_references_card_opener():
    allowed = {APP / "core" / "cardvault.py"}
    offenders = []
    for path in APP.rglob("*.py"):
        if path in allowed or (APP / "delivery") in path.parents:
            continue
        src = path.read_text(encoding="utf-8")
        if "CardOpener" in src or "from_private_bytes" in src:
            offenders.append(str(path))
    assert offenders == []


def test_api_settings_have_no_private_key_field():
    from app.core.config import Settings

    assert not any("private" in name for name in Settings.model_fields)


def test_api_refuses_to_start_with_private_keys(tmp_path):
    env = {**os.environ, "APP_VAULT_PRIVATE_KEYS_FILE": str(tmp_path / "k.json"), "APP_PORT": "0"}
    proc = subprocess.run(
        [sys.executable, "-m", "app.serve"],
        env=env,
        cwd=APP.parent,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert b"refusing to start" in proc.stderr


async def test_ledger_db_trigger_rejects_unbalanced(env):
    async with env.owner_session() as db:
        await db.execute(
            text(
                "INSERT INTO ledger_entries (tenant_id, txn_id, account, amount_minor, currency) "
                "VALUES (:t, gen_random_uuid(), 'x', 100, 'CNY')"
            ),
            {"t": env.tenant_id},
        )
        with pytest.raises(DBAPIError, match="does not balance"):
            await db.commit()


async def test_ledger_service_rejects_unbalanced(env):
    from app.modules.payments.ledger import Line, UnbalancedTransaction, post_txn

    async with env.api_session() as db:
        with pytest.raises(UnbalancedTransaction):
            await post_txn(
                db,
                tenant_id=env.tenant_id,
                currency="CNY",
                memo="x",
                order_id=None,
                lines=[Line("a", 100), Line("b", -99)],
            )
        txn = await post_txn(
            db,
            tenant_id=env.tenant_id,
            currency="CNY",
            memo="ok",
            order_id=None,
            lines=[Line("a", 100), Line("b", -100)],
        )
        await db.commit()
    assert (
        await env.scalar("SELECT sum(amount_minor) FROM ledger_entries WHERE txn_id=:t", t=txn) == 0
    )
