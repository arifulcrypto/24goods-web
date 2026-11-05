"""Delivery-service settings read straight from ``APP_*`` environment variables.

Deliberately not pydantic-based: the delivery process stays small (no pydantic/FastAPI
import) and has a minimal dependency surface next to the vault private keys.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class DeliverySettings:
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
    reveal_per_buyer_cards: int = 30  # decrypted cards per buyer per window
    reveal_per_buyer_window_s: int = 600
    outbox_poll_s: float = 1.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeliverySettings:
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get("APP_" + f.name.upper())
            if raw is None or raw == "":
                continue
            kind = type(f.default)
            try:
                values[f.name] = kind(raw.strip()) if kind is not str else raw
            except ValueError as exc:
                raise ValueError(f"APP_{f.name.upper()} must be {kind.__name__}") from exc
        return cls(**values)  # type: ignore[arg-type]
