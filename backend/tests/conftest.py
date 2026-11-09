"""Test fixtures.

Integration tests need throwaway PostgreSQL (>= 15) and Redis instances:

  TEST_DATABASE_URL       superuser DSN, e.g. postgresql://postgres:test@127.0.0.1:55432/test
                          (CI: postgresql://postgres:postgres@localhost:5432/postgres).
                          A database ``g24_test`` and the role ``app_owner`` are (re)created;
                          the migration then creates ``app_api`` / ``app_delivery``.
  TEST_REDIS_URL          e.g. redis://127.0.0.1:56379/15 (that DB is FLUSHed per test).
  TEST_REQUIRE_SERVICES   "1" -> fail instead of skip when the services are missing (set in CI).

Unit tests (cardvault, epay, money, ...) run without them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# ---- deterministic test configuration (set before app modules read the environment) ----
VAULT_PRIV = X25519PrivateKey.generate()
VAULT_KID = "ktest"
VAULT_PUB_JSON = json.dumps(
    {
        VAULT_KID: base64.b64encode(
            VAULT_PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
    }
)
FP_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
QUOTE_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
POW_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
SETTINGS_KEY = base64.b64encode(secrets.token_bytes(32)).decode()
INTERNAL_TOKEN = secrets.token_urlsafe(32)
ZPAY_PID = "1001"
ZPAY_KEY = "test-merchant-key-0123456789"
BEPUSDT_TOKEN = "test-bepusdt-token-0123456789"
PASSWORD = "correct horse battery"
OWNER_PW = "owner-test-password"
API_PW = "api-test-password"
DELIVERY_PW = "delivery-test-password"
TEST_DB = "g24_test"
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

os.environ.setdefault("APP_ENV", "test")

pytest_plugins: list[str] = []


def _require() -> bool:
    return os.environ.get("TEST_REQUIRE_SERVICES") == "1"


def _with_db(
    dsn: str, user: str, password: str, db: str, driver: str = "postgresql+asyncpg"
) -> str:
    u = urlsplit(dsn)
    host = u.hostname or "127.0.0.1"
    port = f":{u.port}" if u.port else ""
    return urlunsplit((driver, f"{user}:{password}@{host}{port}", f"/{db}", "", ""))


@dataclass
class Services:
    owner_url: str
    api_url: str
    delivery_url: str
    redis_url: str
    upload_dir: Path
    owner_engine: object = None
    api_engine: object = None
    delivery_engine: object = None
    redis: object = None
    pw_hash: str = ""


@pytest.fixture(scope="session")
async def services(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Services]:
    dsn = os.environ.get("TEST_DATABASE_URL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not dsn or not redis_url:
        if _require():
            pytest.fail("TEST_DATABASE_URL / TEST_REDIS_URL are required")
        pytest.skip("integration services not configured (TEST_DATABASE_URL, TEST_REDIS_URL)")

    import asyncpg
    from alembic import command
    from alembic.config import Config

    from app.core.db import make_engine
    from app.core.redis import make_redis
    from app.core.security import hash_password

    plain = dsn.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(plain, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        if _require():
            raise
        pytest.skip(f"PostgreSQL unreachable: {type(exc).__name__}")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'app_owner'")
        if not exists:
            await conn.execute(f"CREATE ROLE app_owner LOGIN CREATEROLE PASSWORD '{OWNER_PW}'")
        else:
            await conn.execute(f"ALTER ROLE app_owner LOGIN CREATEROLE PASSWORD '{OWNER_PW}'")
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB} OWNER app_owner")
    finally:
        await conn.close()

    s = Services(
        owner_url=_with_db(dsn, "app_owner", OWNER_PW, TEST_DB),
        api_url=_with_db(dsn, "app_api", API_PW, TEST_DB),
        delivery_url=_with_db(dsn, "app_delivery", DELIVERY_PW, TEST_DB),
        redis_url=redis_url,
        upload_dir=tmp_path_factory.mktemp("uploads"),
    )
    os.environ.update(
        APP_MIGRATE_DATABASE_URL=s.owner_url,
        APP_DB_API_PASSWORD=API_PW,
        APP_DB_DELIVERY_PASSWORD=DELIVERY_PW,
    )
    cfg = Config(str(ALEMBIC_INI))
    await asyncio.to_thread(command.upgrade, cfg, "head")

    s.owner_engine = make_engine(s.owner_url, pool_size=2, max_overflow=2)
    s.api_engine = make_engine(s.api_url, pool_size=5, max_overflow=5)
    s.delivery_engine = make_engine(s.delivery_url, pool_size=3, max_overflow=2)
    s.redis = make_redis(redis_url)
    s.pw_hash = await hash_password(PASSWORD)
    yield s
    await s.redis.aclose()
    for e in (s.owner_engine, s.api_engine, s.delivery_engine):
        await e.dispose()


TABLES = (
    "after_sales, articles, "
    "audit_log, outbox, ledger_entries, deliveries, card_stock, card_batches, payments, "
    "order_events, "
    "orders, checkouts, quotes, messages, conversations, attachments, products, agent_status, "
    "users, "
    "custom_domains, tenants"
)


def make_settings(s: Services, **over):
    from app.core.config import Settings

    base = dict(
        _env_file=None,
        env="test",
        database_url=s.api_url,
        redis_url=s.redis_url,
        upload_dir=str(s.upload_dir),
        vault_public_keys=VAULT_PUB_JSON,
        vault_active_kid=VAULT_KID,
        vault_fingerprint_key=FP_KEY,
        quote_hmac_key=QUOTE_KEY,
        pow_key=POW_KEY,
        settings_key=SETTINGS_KEY,
        pow_bits=8,
        internal_token=INTERNAL_TOKEN,
        delivery_url="http://delivery",
        zpay_pid=ZPAY_PID,
        zpay_key=ZPAY_KEY,
        bepusdt_url="http://bepusdt.test",
        bepusdt_api_token=BEPUSDT_TOKEN,
        zpay_gateway="https://zpay.example/mockpay",
        zpay_api_base="http://mockpay",
        public_base_url="http://testserver",
        mockpay_notify_base="http://testserver",
        trusted_proxies="127.0.0.1/32",
        # suites predate the #58 limits; tests/test_riskctl_v8.py covers the new ones
        max_pending_orders=5,
        order_create_per_user=1000,
        order_create_per_ip=10000,
    )
    base.update(over)
    return Settings(**base)


class LazyASGI:
    """Late-bound ASGI app (breaks the API <-> mock gateway construction cycle)."""

    def __init__(self) -> None:
        self.app = None

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        assert self.app is not None
        await self.app(scope, receive, send)


@dataclass
class Env:
    s: Services
    res: object
    app: object
    delivery_app: object
    delivery_ctx: object
    mock_app: object
    tenant_id: uuid.UUID
    other_tenant_id: uuid.UUID
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    def client(self, host: str = "testserver", csrf: bool = True) -> httpx.AsyncClient:
        headers = {"X-Requested-With": "fetch"} if csrf else {}
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=f"http://{host}",
            headers=headers,
        )
        self.clients.append(c)
        return c

    def owner_session(self, tenant_id: uuid.UUID | None = None):
        from app.core.db import make_sessionmaker, tenant_session

        return tenant_session(make_sessionmaker(self.s.owner_engine), tenant_id or self.tenant_id)

    def api_session(self, tenant_id: uuid.UUID | None = None):
        from app.core.db import tenant_session

        return tenant_session(self.res.maker, tenant_id or self.tenant_id)

    async def user(
        self,
        role: str = "buyer",
        *,
        email: str | None = None,
        tenant_id: uuid.UUID | None = None,
        name: str | None = None,
        locale: str = "en",
    ):
        from app.models import User

        tid = tenant_id or self.tenant_id
        async with self.owner_session(tid) as db:
            u = User(
                tenant_id=tid,
                email=email or f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                pw_hash=self.s.pw_hash,
                role=role,
                display_name=name or role.title(),
                locale=locale,
            )
            db.add(u)
            await db.commit()
            return u

    async def login(self, role: str = "buyer", **kw) -> tuple[httpx.AsyncClient, object]:
        u = await self.user(role, **kw)
        token = await self.res.sessions.create(u.id, u.tenant_id, u.role)
        host = kw.get("host", "testserver")
        c = self.client(host)
        c.cookies.set("sid", token)
        return c, u

    async def product(
        self,
        *,
        mode: str = "auto_card",
        price: int = 1990,
        floor: int = 1500,
        title: str = "Steam Wallet ¥20",
        zh: str = "Steam 钱包 ¥20",
        category: str = "gift_cards",
        status: str = "active",
        tenant_id: uuid.UUID | None = None,
        desc: str = "Instant code",
    ):
        from app.models import Product

        tid = tenant_id or self.tenant_id
        async with self.owner_session(tid) as db:
            p = Product(
                tenant_id=tid,
                title_i18n={"en": title, "zh-CN": zh},
                description_i18n={"en": desc, "zh-CN": "即时发货"},
                images=[],
                price_minor=price,
                floor_price_minor=floor,
                currency="CNY",
                delivery_mode=mode,
                category=category,
                status=status,
                sold=0,
            )
            db.add(p)
            await db.commit()
            return p

    async def import_codes(self, product, codes: list[str], tenant_id: uuid.UUID | None = None):
        from app.modules.inventory.service import import_cards

        tid = tenant_id or self.tenant_id
        async with self.api_session(tid) as db:
            return await import_cards(
                db,
                self.res.sealer,
                tenant_id=tid,
                product=product,
                lines="\n".join(codes),
                actor_id=None,
                actor_role="admin",
                ip=None,
            )

    async def drain(self) -> int:
        from app.delivery.worker import drain

        return await drain(self.delivery_ctx)

    async def scalar(self, sql: str, **params):
        from sqlalchemy import text

        async with self.owner_session() as db:
            return await db.scalar(text(sql), params)


@pytest.fixture
async def env(services: Services) -> AsyncIterator[Env]:
    from sqlalchemy import text

    from app.core.cardvault import CardOpener
    from app.core.db import make_sessionmaker, tenant_session
    from app.core.resources import build_resources
    from app.delivery.config import DeliverySettings
    from app.delivery.context import DeliveryContext
    from app.delivery.reveal import create_delivery_app
    from app.devtools.mock_zpay import create_mock_app
    from app.main import create_app
    from app.models import Tenant

    s = services
    async with s.owner_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await s.redis.flushdb()
    owner_maker = make_sessionmaker(s.owner_engine)
    async with tenant_session(owner_maker, None) as db:
        # Legacy tests assert exact gateway amounts: they run with no buyer surcharge. The v4
        # tests set (or delete) the config explicitly -- see tests/test_commerce_v4.py.
        t1 = Tenant(
            slug="platform",
            name="Platform",
            settings={"payments": {"channels": {"zpay_alipay": {"surcharge_permille": 0}}}},
        )
        t2 = Tenant(slug="other", name="Other tenant", kind="reseller")
        db.add_all([t1, t2])
        await db.commit()
        tid, tid2 = t1.id, t2.id
        from app.modules.catalog.categories import seed_default_categories

    for t in (tid, tid2):
        async with tenant_session(owner_maker, t) as db:
            await seed_default_categories(db, t)
            await db.commit()

    settings = make_settings(s)
    dsettings = DeliverySettings(
        delivery_database_url=s.delivery_url,
        redis_url=s.redis_url,
        internal_token=INTERNAL_TOKEN,
        reveal_max_per_min=30,
    )
    dctx = DeliveryContext(
        dsettings,
        make_sessionmaker(s.delivery_engine),
        s.redis,
        CardOpener({VAULT_KID: VAULT_PRIV}),
    )
    delivery_app = create_delivery_app(dctx)
    api_proxy = LazyASGI()
    mock_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api_proxy))
    mock_app = create_mock_app(settings, s.redis, mock_http)
    res = build_resources(
        settings,
        engine=s.api_engine,
        redis=s.redis,
        delivery=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=delivery_app), base_url="http://delivery"
        ),
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app)),
    )
    app = create_app(res, background=False)
    api_proxy.app = app
    e = Env(s, res, app, delivery_app, dctx, mock_app, tid, tid2)
    yield e
    for c in e.clients:
        await c.aclose()
    await mock_http.aclose()
    await res.delivery.aclose()
    await res.http.aclose()
    await res.hub.stop()


@pytest.fixture
async def gw(env: Env):  # type: ignore[no-untyped-def]
    """Server-to-server ZPay re-query stub keyed by out_trade_no (see tests.helpers.Gateway)."""
    from tests.helpers import Gateway

    stub = Gateway()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handler))
    env.res.zpay._http = client
    yield stub
    await client.aclose()
