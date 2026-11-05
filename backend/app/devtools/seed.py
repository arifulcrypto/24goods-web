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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker, tenant_session
from app.core.logging import configure_logging
from app.core.security import hash_password, verify_password
from app.core.vaultkeys import build_sealer
from app.devtools.covers import cover_png
from app.models import AgentStatus, CardStock, Conversation, Product, Tenant, User
from app.modules.admin.service import set_agent_status
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
            t = Tenant(slug=settings.default_tenant_slug, name="24goods", kind="platform")
            db.add(t)
            await db.commit()
            tid = t.id
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
            )
            db.add(p)
            await db.commit()
        out[sp.en] = p
    return out


async def run(settings: Settings, database_url: str) -> None:
    admin_pw = _password("APP_SEED_ADMIN_PASSWORD")
    agent_pw = _password("APP_SEED_AGENT_PASSWORD")
    buyer_pw = _password("APP_SEED_BUYER_PASSWORD")
    sealer = build_sealer(settings)
    engine = make_engine(database_url, pool_size=2, max_overflow=0)
    maker = make_sessionmaker(engine)
    store = FileStore(settings.upload_dir)
    try:
        tid = await _tenant(maker, settings)
        async with tenant_session(maker, tid) as db:
            admin = await _user(db, tid, "admin@demo.local", "admin", "Admin", "en", admin_pw)
            agent = await _user(
                db, tid, "agent@demo.local", "agent", "Lily (Support)", "en", agent_pw
            )
            buyer = await _user(db, tid, "buyer@demo.local", "buyer", "Demo Buyer", "en", buyer_pw)
            if await db.get(AgentStatus, agent.id) is None:
                await set_agent_status(db, agent, "online", "Usually replies in 5 minutes")
            products = await _products(db, tid, admin, store)
            for p in products.values():
                if p.delivery_mode != "auto_card":
                    continue
                n = await db.scalar(
                    select(func.count(CardStock.id)).where(CardStock.product_id == p.id)
                )
                if not n:
                    codes = "\n".join(demo_code() for _ in range(CARDS_PER_PRODUCT))
                    await import_cards(
                        db,
                        sealer,
                        tenant_id=tid,
                        product=p,
                        lines=codes,
                        actor_id=admin.id,
                        actor_role="admin",
                        ip=None,
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
