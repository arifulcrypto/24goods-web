"""``python -m app.devtools.seed`` — idempotent demo data.

Connects as the schema owner (``APP_MIGRATE_DATABASE_URL``, falling back to
``APP_DATABASE_URL``). Passwords come from ``APP_SEED_ADMIN_PASSWORD``,
``APP_SEED_AGENT_PASSWORD`` and ``APP_SEED_BUYER_PASSWORD`` (>= 12 chars each).
Card codes are imported through the same seal-only path as the admin API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker, tenant_session
from app.core.logging import configure_logging
from app.core.markdown import render_i18n
from app.core.security import hash_password, verify_password
from app.core.vaultkeys import build_sealer
from app.devtools.covers import cover_png
from app.devtools.seed_content import (
    ANNOUNCEMENT,
    ANNOUNCEMENT_PROD,
    ARTICLES,
    PAGES,
    SUPPORT_HOURS,
)
from app.models import AgentStatus, Article, CardStock, Conversation, Product, Tenant, User
from app.modules.admin.service import set_agent_status
from app.modules.catalog.categories import seed_default_categories
from app.modules.chat import quotes as quote_svc
from app.modules.chat import service as chat
from app.modules.chat.schemas import QuoteIn
from app.modules.common.views import file_url
from app.modules.inventory.service import import_cards
from app.modules.uploads.processing import process_image
from app.modules.uploads.service import store_attachment
from app.modules.uploads.storage import FileStore

log = logging.getLogger("app.seed")

CARDS_PER_PRODUCT = 20


@dataclass(frozen=True)
class SeedProduct:
    en: str
    zh: str
    desc_en: str
    desc_zh: str
    category: str
    mode: str
    price: int  # minor units
    floor: int
    initials: str
    colors: tuple[tuple[int, int, int], tuple[int, int, int]]
    delivery_format: str | None = None
    delivery_separator: str = "----"
    redeem_url: str | None = None
    usage_en: str = ""
    usage_zh: str = ""
    after_sales_hours: int | None = None
    group_sort: int = 0
    multi_field: bool = False  # demo cards are `account----password----2fa----country`
    batch_note: str | None = None
    batch_days: int | None = None  # expiry of the demo batch


PRODUCTS: list[SeedProduct] = [
    SeedProduct(
        "Steam Wallet Code ¥50",
        "Steam 钱包充值码 ¥50",
        "Instant digital code, redeem in the Steam client.",
        "即时发货，在 Steam 客户端兑换。",
        "gift_cards",
        "auto_card",
        5200,
        4800,
        "ST",
        ((23, 26, 33), (102, 192, 244)),
        delivery_format="Code",
        redeem_url="https://store.steampowered.com/account/redeemwalletcode",
        usage_en="Open Steam, go to **Account details -> Redeem a Steam Wallet code**, "
        "and paste the code. Each code works once, in the matching region only.",
        usage_zh="打开 Steam，进入 **账户明细 -> 兑换 Steam 钱包码**，粘贴卡密即可。"
        "每个卡密仅可使用一次，且仅限对应地区。",
        group_sort=1,
    ),
    SeedProduct(
        "【Premium】Gmail Account — aged, with 2FA",
        "【优质】Gmail 老号 — 带 2FA",
        "Aged Gmail account with recovery 2FA key. Delivered as "
        "Account----Password----2FA----Notes.",
        "带 2FA 密钥的 Gmail 老号，发货格式：账号----密码----2FA----备注。",
        "accounts",
        "auto_card",
        800,
        600,
        "GM",
        ((190, 40, 40), (250, 190, 60)),
        delivery_format="Account----Password----2FA----Notes",
        redeem_url="https://accounts.google.com/",
        usage_en="Log in **within 24 hours** of delivery. Change the password and add your own "
        "recovery email first. Keep the 2FA key private.",
        usage_zh="请在发货后 **24 小时内** 登录，先修改密码并绑定您自己的辅助邮箱，"
        "2FA 密钥请勿泄露。",
        after_sales_hours=24,
        multi_field=True,
        batch_note="Demo batch (expires in 90 days)",
        batch_days=90,
    ),
    SeedProduct(
        "Apple Gift Card ¥100",
        "Apple 礼品卡 ¥100",
        "Works for App Store, iCloud+ and Apple Music (CN region).",
        "适用于 App Store、iCloud+ 与 Apple Music（中国区）。",
        "gift_cards",
        "auto_card",
        9900,
        9500,
        "AP",
        ((80, 80, 90), (200, 200, 210)),
    ),
    SeedProduct(
        "Honor of Kings 60 Tokens",
        "王者荣耀 60 点券",
        "Auto-delivered top-up code.",
        "自动发货充值码。",
        "game_topup",
        "auto_card",
        580,
        500,
        "HK",
        ((140, 30, 30), (240, 170, 60)),
    ),
    SeedProduct(
        "Genshin Impact 6480 Genesis Crystals",
        "原神 6480 创世结晶",
        "Top-up by UID, delivered by an agent within 30 minutes.",
        "凭 UID 代充，客服 30 分钟内处理。",
        "game_topup",
        "manual",
        32800,
        30000,
        "GI",
        ((40, 60, 140), (150, 200, 255)),
    ),
    SeedProduct(
        "Windows 11 Pro Retail Key",
        "Windows 11 专业版零售密钥",
        "Genuine retail activation key, lifetime.",
        "正版零售激活密钥，永久有效。",
        "software_keys",
        "auto_card",
        3990,
        3500,
        "W11",
        ((0, 90, 180), (0, 170, 240)),
    ),
    SeedProduct(
        "JetBrains All Products Pack — 1 month",
        "JetBrains 全家桶 1 个月",
        "Activation code for all JetBrains IDEs.",
        "适用于全部 JetBrains IDE 的激活码。",
        "software_keys",
        "auto_card",
        6900,
        6000,
        "JB",
        ((20, 20, 20), (250, 80, 140)),
    ),
    SeedProduct(
        "Custom Logo Design (3 concepts)",
        "定制 Logo 设计（3 个方案）",
        "A designer delivers 3 concepts in 48 h; one round of revisions.",
        "设计师 48 小时内交付 3 个方案，含一次修改。",
        "design_services",
        "manual",
        19900,
        15000,
        "LG",
        ((120, 40, 160), (250, 120, 200)),
        usage_en="Send your brief in the chat (brand name, style, colours). We deliver 3 "
        "concepts within 48 hours.",
        usage_zh="请在对话中发送需求（品牌名、风格、配色），我们将在 48 小时内交付 3 个方案。",
        after_sales_hours=168,
    ),
    SeedProduct(
        "Resume Polishing (EN / 中文)",
        "简历润色（中英文）",
        "Professional rewrite of your CV in English or Chinese.",
        "专业中英文简历润色与改写。",
        "design_services",
        "manual",
        8900,
        7000,
        "CV",
        ((20, 110, 90), (120, 220, 180)),
    ),
    SeedProduct(
        "Notion Plus workspace — 1 year",
        "Notion Plus 工作区 1 年",
        "Upgrade of your own workspace, handled by an agent.",
        "为您的工作区升级，客服人工处理。",
        "accounts",
        "manual",
        12800,
        11000,
        "NO",
        ((30, 30, 30), (160, 160, 160)),
    ),
    SeedProduct(
        "Duolingo Super family seat — 1 month",
        "多邻国 Super 家庭席位 1 个月",
        "Invite to a family plan seat.",
        "家庭计划席位邀请。",
        "accounts",
        "manual",
        1800,
        1500,
        "DU",
        ((60, 150, 20), (170, 230, 90)),
    ),
    SeedProduct(
        "Midjourney Basic — 1 month gift code",
        "Midjourney 基础版 1 个月礼品码",
        "Gift code for one month of Midjourney Basic.",
        "Midjourney 基础版一个月礼品码。",
        "ai_tools",
        "auto_card",
        7900,
        7000,
        "MJ",
        ((10, 10, 40), (90, 90, 220)),
    ),
    SeedProduct(
        "ChatGPT Plus — 1 month (top-up service)",
        "ChatGPT Plus 1 个月（代充服务）",
        "We top up your own account; an agent will contact you.",
        "为您自己的账号代充，客服会与您联系。",
        "ai_tools",
        "manual",
        15800,
        14000,
        "AI",
        ((16, 163, 127), (180, 240, 220)),
    ),
]


def demo_account() -> str:
    alphabet = string.ascii_lowercase + string.digits
    name = "".join(secrets.choice(alphabet) for _ in range(8))
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    key = "".join(secrets.choice(string.ascii_lowercase + "234567") for _ in range(16))
    return f"demo.{name}@gmail.com----{pw}----{key}----US"


def demo_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "DEMO-" + "-".join(groups)


def _password(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 12:
        raise SystemExit(f"{name} is required (>= 12 characters)")
    return value


async def _tenant(maker, settings: Settings) -> uuid.UUID:  # type: ignore[no-untyped-def]
    async with tenant_session(maker, None) as db:
        tid = await db.scalar(select(Tenant.id).where(Tenant.slug == settings.default_tenant_slug))
        if tid is None:
            t = Tenant(slug=settings.default_tenant_slug, name=settings.brand_name, kind="platform")
            db.add(t)
            await db.commit()
            tid = t.id
        else:  # rename the pre-v3 demo tenant
            tenant = await db.get(Tenant, tid)
            if tenant is not None and tenant.name != settings.brand_name:
                tenant.name = settings.brand_name
                await db.commit()
        return tid


async def _user(
    db: AsyncSession, tid: uuid.UUID, email: str, role: str, name: str, locale: str, pw: str
) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            tenant_id=tid,
            email=email,
            pw_hash=await hash_password(pw),
            role=role,
            display_name=name,
            locale=locale,
        )
        db.add(user)
    else:
        ok, _ = await verify_password(user.pw_hash, pw)
        if not ok:
            user.pw_hash = await hash_password(pw)
        user.role = role
        user.status = "active"
    await db.commit()
    return user


async def _products(
    db: AsyncSession, tid: uuid.UUID, admin: User, store: FileStore
) -> dict[str, Product]:
    out: dict[str, Product] = {}
    for sp in PRODUCTS:
        p = await db.scalar(select(Product).where(Product.title_i18n["en"].astext == sp.en))
        if p is None:
            processed = await asyncio.to_thread(process_image, cover_png(sp.initials, *sp.colors))
            att = await store_attachment(
                db,
                store,
                tenant_id=tid,
                owner_id=admin.id,
                kind="image",
                processed=processed,
                purpose="product",
                is_public=True,
            )
            p = Product(
                tenant_id=tid,
                title_i18n={"en": sp.en, "zh-CN": sp.zh},
                description_i18n={"en": sp.desc_en, "zh-CN": sp.desc_zh},
                images=[file_url(att.id)],
                price_minor=sp.price,
                floor_price_minor=sp.floor,
                currency="CNY",
                delivery_mode=sp.mode,
                category=sp.category,
                status="active",
                sold=0,
                created_by=admin.id,
                delivery_format=sp.delivery_format,
                delivery_separator=sp.delivery_separator,
                redeem_url=sp.redeem_url,
                usage_i18n={"en": sp.usage_en, "zh-CN": sp.usage_zh},
                usage_html_i18n=render_i18n({"en": sp.usage_en, "zh-CN": sp.usage_zh}),
                after_sales_hours=sp.after_sales_hours,
                group_sort=sp.group_sort,
            )
            db.add(p)
            await db.commit()
        elif p.delivery_format is None and (sp.delivery_format or sp.usage_en):
            # upgrade a pre-v3 demo product in place (idempotent)
            p.delivery_format = sp.delivery_format
            p.redeem_url = sp.redeem_url
            p.usage_i18n = {"en": sp.usage_en, "zh-CN": sp.usage_zh}
            p.usage_html_i18n = render_i18n(p.usage_i18n)
            p.after_sales_hours = sp.after_sales_hours
            p.group_sort = sp.group_sort
            await db.commit()
        out[sp.en] = p
    return out


async def _content(db: AsyncSession, tid: uuid.UUID, admin: User, *, demo: bool = True) -> None:
    """Fixed pages, tutorial articles, announcement v1 (created once, never overwritten)."""
    for spec in PAGES:
        if await db.scalar(select(Article.id).where(Article.slug == spec["slug"])) is None:
            db.add(
                Article(
                    tenant_id=tid,
                    slug=spec["slug"],
                    kind="page",
                    category="page",
                    title_i18n=spec["title"],
                    body_i18n=spec["body"],
                    body_html_i18n=render_i18n(spec["body"]),
                    status="published",
                    published_at=datetime.now(timezone.utc),
                    created_by=admin.id,
                )
            )
    for spec in ARTICLES:
        if await db.scalar(select(Article.id).where(Article.slug == spec["slug"])) is None:
            db.add(
                Article(
                    tenant_id=tid,
                    slug=spec["slug"],
                    kind="article",
                    category=spec["category"],
                    title_i18n=spec["title"],
                    body_i18n=spec["body"],
                    body_html_i18n=render_i18n(spec["body"]),
                    pinned=spec["pinned"],
                    status="published",
                    published_at=datetime.now(timezone.utc),
                    created_by=admin.id,
                )
            )
    tenant = await db.get(Tenant, tid)
    if tenant is not None and "announcement" not in (tenant.settings or {}):
        ann = dict(ANNOUNCEMENT if demo else ANNOUNCEMENT_PROD)
        ann["body_html_i18n"] = render_i18n(ann["body_i18n"])
        tenant.settings = {
            **(tenant.settings or {}),
            "announcement": ann,
            "support_hours": SUPPORT_HOURS,
        }
    await db.commit()


async def backfill_description_html(db: AsyncSession) -> int:
    """Render ``description_html_i18n`` from the markdown descriptions (idempotent)."""
    n = 0
    for p in await db.scalars(select(Product)):
        html = render_i18n(p.description_i18n)
        if p.description_html_i18n != html:
            p.description_html_i18n = html
            n += 1
    await db.commit()
    return n


async def _run_prod(settings: Settings, database_url: str, admin_pw: str) -> None:
    """Production seed: tenant, ONE admin, fixed pages, articles, site settings. No demo data."""
    engine = make_engine(database_url, pool_size=2, max_overflow=0)
    maker = make_sessionmaker(engine)
    try:
        tid = await _tenant(maker, settings)
        async with tenant_session(maker, tid) as db:
            await seed_default_categories(db, tid)
            await db.commit()
            email = settings.seed_admin_email.strip().lower()
            existing = await db.scalar(select(User).where(User.email == email))
            if existing is None:
                admin = await _user(db, tid, email, "admin", "Admin", "en", admin_pw)
            else:  # never reset a password the admin may have changed
                admin = existing
            await _content(db, tid, admin, demo=False)
            await backfill_description_html(db)
        log.info("prod seed complete: tenant=%s admin=%s", tid, email)
    finally:
        await engine.dispose()


async def run(settings: Settings, database_url: str) -> None:
    admin_pw = _password("APP_SEED_ADMIN_PASSWORD")
    if not settings.seed_demo:
        await _run_prod(settings, database_url, admin_pw)
        return
    agent_pw = _password("APP_SEED_AGENT_PASSWORD")
    buyer_pw = _password("APP_SEED_BUYER_PASSWORD")
    sealer = build_sealer(settings)
    engine = make_engine(database_url, pool_size=2, max_overflow=0)
    maker = make_sessionmaker(engine)
    store = FileStore(settings.upload_dir)
    try:
        tid = await _tenant(maker, settings)
        async with tenant_session(maker, tid) as db:
            await seed_default_categories(db, tid)
            await db.commit()
            admin = await _user(db, tid, "admin@demo.local", "admin", "Admin", "en", admin_pw)
            agent = await _user(
                db, tid, "agent@demo.local", "agent", "Lily (Support)", "en", agent_pw
            )
            buyer = await _user(db, tid, "buyer@demo.local", "buyer", "Demo Buyer", "en", buyer_pw)
            if await db.get(AgentStatus, agent.id) is None:
                await set_agent_status(db, agent, "online", "Usually replies in 5 minutes")
            await _content(db, tid, admin)
            products = await _products(db, tid, admin, store)
            await backfill_description_html(db)
            for p in products.values():
                if p.delivery_mode != "auto_card":
                    continue
                n = await db.scalar(
                    select(func.count(CardStock.id)).where(CardStock.product_id == p.id)
                )
                if not n:
                    sp = next(s for s in PRODUCTS if s.en == p.title_i18n["en"])
                    gen = demo_account if sp.multi_field else demo_code
                    codes = "\n".join(gen() for _ in range(CARDS_PER_PRODUCT))
                    expiry = (
                        datetime.now(timezone.utc) + timedelta(days=sp.batch_days)
                        if sp.batch_days
                        else None
                    )
                    await import_cards(
                        db,
                        sealer,
                        tenant_id=tid,
                        product=p,
                        lines=codes,
                        actor_id=admin.id,
                        actor_role="admin",
                        ip=None,
                        note=sp.batch_note or "Demo stock",
                        expires_at=expiry,
                    )
                    del codes
            has_conv = await db.scalar(
                select(func.count(Conversation.id)).where(Conversation.buyer_id == buyer.id)
            )
            if not has_conv:
                logo = products["Custom Logo Design (3 concepts)"]
                conv, _ = await chat.find_or_create(db, buyer, logo.id)
                await chat.send_user_message(
                    db,
                    conv,
                    buyer,
                    role="buyer",
                    type="text",
                    text="Hi! Can you do a minimalist logo for my coffee shop?",
                    attachment_id=None,
                    client_msg_id="seed-buyer-1",
                )
                await chat.send_user_message(
                    db,
                    conv,
                    agent,
                    role="agent",
                    type="text",
                    text="Sure! For a single-color minimalist mark I can offer a discount.",
                    attachment_id=None,
                    client_msg_id="seed-agent-1",
                )
                await quote_svc.create_quote(
                    db,
                    settings,
                    conv,
                    agent,
                    QuoteIn(
                        title="Minimalist logo — 3 concepts",
                        amount="179.00",
                        qty=1,
                        note="Single-color, delivered in 48 h",
                        product_id=logo.id,
                        expires_in_minutes=1440,
                    ),
                    None,
                )
        log.info("seed complete: tenant=%s products=%d", tid, len(PRODUCTS))
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    url = os.environ.get("APP_MIGRATE_DATABASE_URL") or settings.database_url
    asyncio.run(run(settings, url))


if __name__ == "__main__":
    main()
