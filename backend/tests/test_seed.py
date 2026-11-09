"""The demo seed (run as the schema owner, like deploy/bootstrap.sh does)."""

from __future__ import annotations

import os

from sqlalchemy import text

from app.devtools import seed


async def test_seed_populates_v3_demo_data_idempotently(env, monkeypatch):
    for name in ("ADMIN", "AGENT", "BUYER"):
        monkeypatch.setenv(f"APP_SEED_{name}_PASSWORD", "seed-password-123")
    async with env.owner_session() as db:  # the seed brings its own tenant row
        await db.execute(text("UPDATE tenants SET slug = 'old' WHERE slug = 'platform'"))
        await db.commit()
    settings = env.res.settings
    url = env.s.owner_url
    assert os.environ.get("APP_SEED_ADMIN_PASSWORD")
    await seed.run(settings, url)
    await seed.run(settings, url)  # idempotent

    async with env.owner_session() as db:
        tid = await db.scalar(text("SELECT id FROM tenants WHERE slug='platform'"))
        name = await db.scalar(text("SELECT name FROM tenants WHERE slug='platform'"))
    assert name == "Kesiv"
    async with env.owner_session(tid) as db:
        pages = {
            r[0]
            for r in (await db.execute(text("SELECT slug FROM articles WHERE kind='page'"))).all()
        }
        arts = (
            await db.execute(text("SELECT count(*) FROM articles WHERE kind='article'"))
        ).scalar()
        assert pages == {"help", "rules", "after-sales", "terms", "privacy"} and arts == 3
        terms = (
            await db.execute(text("SELECT body_i18n FROM articles WHERE slug='terms'"))
        ).scalar()
        # issue #48 §5: no internal "have a lawyer review this" banner on a live site
        assert "lawyer" not in terms["en"] and "律师" not in terms["zh-CN"]
        assert "Mainland China" in terms["en"] and "中国大陆" in terms["zh-CN"]
        assert terms["en"].startswith("_Effective date:")
        ann = (
            await db.execute(
                text("SELECT settings->'announcement' FROM tenants WHERE id=:t"), {"t": tid}
            )
        ).scalar()
        assert ann["version"] == 1 and ann["enabled"] is True
        acct = (
            await db.execute(
                text(
                    "SELECT delivery_format, after_sales_hours FROM products "
                    "WHERE title_i18n->>'en' LIKE '%Gmail%'"
                )
            )
        ).one()
        assert acct == ("Account----Password----2FA----Notes", 24)
        steam = (
            await db.execute(
                text("SELECT redeem_url FROM products WHERE title_i18n->>'en' LIKE 'Steam%'")
            )
        ).scalar()
        assert steam == "https://store.steampowered.com/account/redeemwalletcode"
        assert (
            await db.execute(text("SELECT count(*) FROM card_batches WHERE expires_at IS NOT NULL"))
        ).scalar() == 1
        assert (
            await db.execute(text("SELECT count(*) FROM card_stock WHERE lookup_fp IS NULL"))
        ).scalar() == 0
    # public API serves it
    pub = env.client()
    site = (await pub.get("/api/v1/site")).json()
    assert site["brand"] == "Kesiv" and site["announcement"]["version"] == 1
    assert {p["slug"] for p in site["pages"]} == {
        "help",
        "rules",
        "after-sales",
        "terms",
        "privacy",
    }


async def test_seed_prod_mode_creates_only_admin_and_content(env, monkeypatch):
    monkeypatch.setenv("APP_SEED_ADMIN_PASSWORD", "seed-password-123")
    async with env.owner_session() as db:
        await db.execute(text("UPDATE tenants SET slug = 'old' WHERE slug = 'platform'"))
        await db.commit()
    settings = env.res.settings.model_copy(
        update={"seed_demo": False, "seed_admin_email": "Admin@Kesiv.com"}
    )
    url = env.s.owner_url
    await seed.run(settings, url)
    async with env.owner_session() as db:
        tid = await db.scalar(text("SELECT id FROM tenants WHERE slug='platform'"))
    async with env.owner_session(tid) as db:
        users = (await db.execute(text("SELECT email, role FROM users"))).all()
        assert users == [("admin@kesiv.com", "admin")]
        for table in ("products", "card_stock", "conversations", "orders"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0  # noqa: S608
        assert (await db.execute(text("SELECT count(*) FROM articles"))).scalar() == 8
        # admin edits must survive a re-run
        await db.execute(
            text("UPDATE articles SET title_i18n='{\"en\":\"Edited\"}' WHERE slug='help'")
        )
        await db.execute(
            text(
                "UPDATE tenants SET settings = "
                "jsonb_set(settings, '{announcement,enabled}', 'true')"
            )
        )
        await db.commit()
    await seed.run(settings, url)
    async with env.owner_session(tid) as db:
        assert (await db.execute(text("SELECT count(*) FROM users"))).scalar() == 1
        assert (await db.execute(text("SELECT count(*) FROM articles"))).scalar() == 8
        assert (
            await db.execute(text("SELECT title_i18n->>'en' FROM articles WHERE slug='help'"))
        ).scalar() == "Edited"
        ann = (
            await db.execute(
                text("SELECT settings->'announcement' FROM tenants WHERE id=:t"), {"t": tid}
            )
        ).scalar()
        assert ann["enabled"] is True
        assert "demo" not in str(ann).lower() and "mock" not in str(ann).lower()
