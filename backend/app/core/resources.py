"""Process-wide resources shared by the public and internal apps."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.cardvault import CardSealer
from app.core.config import Settings
from app.core.db import make_engine, make_sessionmaker
from app.core.redis import make_redis
from app.core.security import SessionStore
from app.core.tenancy import TenantResolver
from app.core.vaultkeys import build_sealer
from app.modules.realtime.hub import RealtimeHub
from app.payments.zpay import ZPayClient, ZPayConfig


@dataclass
class Resources:
    settings: Settings
    engine: AsyncEngine
    maker: async_sessionmaker[AsyncSession]
    redis: Redis
    tenants: TenantResolver
    sessions: SessionStore
    sealer: CardSealer
    zpay: ZPayClient
    delivery: httpx.AsyncClient
    http: httpx.AsyncClient
    hub: RealtimeHub

    async def close(self) -> None:
        await self.hub.stop()
        await self.delivery.aclose()
        await self.http.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


def zpay_config(settings: Settings) -> ZPayConfig:
    return ZPayConfig(
        pid=settings.zpay_pid,
        key=settings.zpay_key,
        gateway=settings.zpay_gateway,
        api_base=settings.zpay_query_base,
        notify_url=settings.notify_url,
        return_url=settings.return_url,
    )


def build_resources(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    redis: Redis | None = None,
    delivery: httpx.AsyncClient | None = None,
    http: httpx.AsyncClient | None = None,
) -> Resources:
    engine = engine or make_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    settings.pow_secret()  # fail fast: APP_POW_KEY is mandatory in prod
    settings.lookup_secret()
    maker = make_sessionmaker(engine)
    redis = redis or make_redis(settings.redis_url)
    http = http or httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    delivery = delivery or httpx.AsyncClient(base_url=settings.delivery_url, timeout=10.0)
    return Resources(
        settings=settings,
        engine=engine,
        maker=maker,
        redis=redis,
        tenants=TenantResolver(maker, settings.default_tenant_slug),
        sessions=SessionStore(redis, settings),
        sealer=build_sealer(settings),
        zpay=ZPayClient(zpay_config(settings), http),
        delivery=delivery,
        http=http,
        hub=RealtimeHub(redis),
    )
