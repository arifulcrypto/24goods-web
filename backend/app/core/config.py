"""Runtime configuration (environment variables with the ``APP_`` prefix).

The public API process loads ONLY public vault material (``APP_VAULT_PUBLIC_KEYS``
or ``APP_VAULT_PUBLIC_KEYS_FILE``). Private keys are loaded exclusively by the
delivery service (see ``app/delivery/config.py``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
from functools import cached_property, lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def decode_key(value: str, *, min_len: int = 32) -> bytes:
    """Decode a base64 (std or url-safe) secret and enforce a minimum length."""
    raw = value.strip()
    try:
        padded = raw + "=" * (-len(raw) % 4)
        key = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("key must be base64") from exc
    if len(key) < min_len:
        raise ValueError(f"key must decode to >= {min_len} bytes")
    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | staging | prod
    log_level: str = "INFO"

    # --- seed (python -m app.devtools.seed) ---
    seed_demo: bool = True  # false = production seed: tenant + one admin + fixed content only
    seed_admin_email: str = "admin@kesiv.com"  # prod seed only (demo seed uses admin@demo.local)

    # --- network ---
    host: str = "127.0.0.1"  # the Docker image sets APP_HOST=0.0.0.0
    port: int = 8000
    internal_host: str = "127.0.0.1"
    internal_port: int = 8081
    trusted_proxies: str = "127.0.0.1/32,::1/128"
    public_base_url: str = "http://localhost:8080"

    # --- storage ---
    database_url: str = "postgresql+asyncpg://app_api:app_api@localhost:5432/app"
    db_pool_size: int = 4
    db_max_overflow: int = 4
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "/data/uploads"

    # --- sessions ---
    cookie_secure: bool = False
    session_ttl_buyer_s: int = 7 * 24 * 3600
    session_ttl_staff_s: int = 12 * 3600

    # --- tenancy ---
    default_tenant_slug: str = "platform"

    # --- vault (public half only) ---
    vault_public_keys: str = ""  # JSON {kid: base64 raw X25519 public key}
    vault_public_keys_file: str = ""
    vault_active_kid: str = ""
    vault_fingerprint_key: str = ""  # base64, >= 32 bytes
    quote_hmac_key: str = ""  # base64, >= 32 bytes
    pow_key: str = ""  # base64, >= 32 bytes: signs proof-of-work challenges
    settings_key: str = ""  # base64, 32 bytes: AES-256-GCM key for tenant_secrets (api only)
    lookup_key: str = (
        ""  # base64, >= 32 bytes: HMAC for order access codes (dev: derived; required in prod)
    )

    # --- internal services ---
    internal_token: str = ""  # shared secret API -> delivery, >= 32 chars
    delivery_url: str = "http://127.0.0.1:8001"

    # --- orders ---
    order_ttl_minutes: int = 30
    max_pending_orders: int = 5  # per buyer
    reveal_per_buyer_cards: int = 30  # cards revealed per buyer per window (API side)
    reveal_per_buyer_window_s: int = 600
    expiry_interval_s: int = 60
    max_pending_orders_instant: int = 2
    order_source_max: int = 32

    # --- storefront v3 ---
    brand_name: str = "Kesiv"
    pow_bits: int = 18  # leading zero bits required (~0.3-1 s on a phone)
    pow_ttl_s: int = 120
    instant_per_ip_hour: int = 3
    instant_per_ip_day: int = 10
    instant_per_net_day: int = 200  # hard cap per /24 (IPv4) or /48 (IPv6)
    # soft ramp: busy networks (CGNAT, offices) are slowed by harder PoW before the cap bites
    instant_ramp_1: int = 30  # > this many instant accounts today on the net: +4 bits
    instant_ramp_2: int = 100  # > this many: +6 bits
    instant_purge_days: int = 7
    lookup_per_ip_hour: int = 10
    lookup_failures_per_order_hour: int = 5  # per (order, ip)
    lookup_failures_global_order_hour: int = 50  # per order, all IPs
    card_expiry_interval_s: int = 3600

    # --- payments (ZPay / EPay) ---
    zpay_pid: str = ""
    zpay_key: str = ""
    # Browser-facing base for submit.php. May be relative in dev/demo ("/mockpay"); the
    # resulting pay_url is then relative too. Must be https:// in prod.
    zpay_gateway: str = "https://zpayz.cn"
    zpay_api_base: str = ""  # server-to-server base for api.php (defaults to gateway)
    zpay_fee_permille: int = 16  # 1.6 %
    notify_rate_per_min: int = 120  # per source IP on /pay/notify/*

    # --- dev mock gateway ---
    mockpay_host: str = "127.0.0.1"
    mockpay_port: int = 8002
    mockpay_notify_base: str = ""

    # --- tls ask endpoint ---
    tls_ask_rate_per_min: int = 30

    @field_validator("env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in {"dev", "test", "staging", "prod"}:
            raise ValueError("APP_ENV must be dev|test|staging|prod")
        return v

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        if self.env == "prod":
            if not self.zpay_gateway.startswith("https://"):
                raise ValueError("APP_ZPAY_GATEWAY must be an https:// URL in prod")
            if self.zpay_api_base and not self.zpay_api_base.startswith("https://"):
                raise ValueError("APP_ZPAY_API_BASE must be https:// in prod (it carries the key)")
            if not self.cookie_secure:
                raise ValueError("APP_COOKIE_SECURE must be true in prod")

        return self

    @cached_property
    def trusted_proxy_networks(self) -> tuple[Network, ...]:
        return tuple(
            ipaddress.ip_network(part.strip(), strict=False)
            for part in self.trusted_proxies.split(",")
            if part.strip()
        )

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def zpay_query_base(self) -> str:
        return (self.zpay_api_base or self.zpay_gateway).rstrip("/")

    @property
    def notify_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/v1/pay/notify/zpay"

    @property
    def return_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/v1/pay/return/zpay"

    def quote_key(self) -> bytes:
        return decode_key(self.quote_hmac_key)

    def pow_secret(self) -> bytes:
        if self.pow_key.strip():
            return decode_key(self.pow_key)
        if self.is_prod:
            raise ValueError("APP_POW_KEY is required in prod")
        return hashlib.sha256(b"dev-pow\x00" + self.quote_key()).digest()

    def settings_secret(self) -> bytes:
        """32-byte AES-GCM key for ``tenant_secrets`` (required in prod; derived in dev/test)."""
        if self.settings_key.strip():
            key = decode_key(self.settings_key)
            if len(key) != 32:
                raise ValueError("APP_SETTINGS_KEY must decode to exactly 32 bytes")
            return key
        if self.is_prod:
            raise ValueError("APP_SETTINGS_KEY is required in prod")
        return hmac.new(self.pow_secret(), b"settings-secretbox/v1", hashlib.sha256).digest()

    def lookup_secret(self) -> bytes:
        if self.lookup_key.strip():
            return decode_key(self.lookup_key)
        if self.is_prod:
            raise ValueError("APP_LOOKUP_KEY is required in prod")
        return hmac.new(self.pow_secret(), b"order-access-code/v1", hashlib.sha256).digest()

    def fingerprint_key(self) -> bytes:
        return decode_key(self.vault_fingerprint_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
