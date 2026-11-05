from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DeliverySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    delivery_database_url: str = "postgresql+asyncpg://app_delivery:app_delivery@localhost:5432/app"
    redis_url: str = "redis://localhost:6379/0"
    vault_private_keys_file: str = ""  # JSON {kid: base64 raw X25519 private key}, chmod 600
    internal_token: str = ""
    delivery_host: str = "127.0.0.1"
    delivery_port: int = 8001
    reveal_max_per_min: int = 30
    reveal_per_order_per_hour: int = 10
    outbox_poll_s: float = 1.0
