from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cardvault import CardOpener
from app.delivery.config import DeliverySettings


@dataclass
class DeliveryContext:
    settings: DeliverySettings
    maker: async_sessionmaker[AsyncSession]
    redis: Redis
    opener: CardOpener
