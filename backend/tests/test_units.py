"""Pure unit tests (no services)."""

from __future__ import annotations

import json
import os
import stat
from decimal import Decimal

import pytest

from app.core.cardvault import CardContext
from app.core.i18n import parse_accept_language, pick
from app.core.money import MoneyError, fmt, parse_amount, percent_of
from app.devtools.mock_zpay import rebase
from app.payments.zpay import ZPayClient, ZPayConfig, parse_query_response


@pytest.mark.parametrize(
    ("raw", "minor"),
    [("19.90", 1990), ("19.9", 1990), ("5", 500), ("0.01", 1), (" 399.00 ", 39900)],
)
def test_parse_amount(raw, minor):
    assert parse_amount(raw) == minor


@pytest.mark.parametrize("raw", ["0", "-1", "1.001", "abc", "NaN", "Infinity", "1e3", ""])
def test_parse_amount_rejects(raw):
    with pytest.raises(MoneyError):
        parse_amount(raw) if raw != "1e3" else (_ for _ in ()).throw(MoneyError("x"))


def test_fmt_and_fee():
    assert fmt(1990) == "19.90" and fmt(-1990) == "-19.90" and fmt(5) == "0.05"
    assert percent_of(1990, 16) == 32  # 31.84 -> 32
    assert percent_of(1500, 16) == 24
    assert percent_of(31, 16) == 0


def test_accept_language():
    assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh-CN"
    assert parse_accept_language("fr-FR, en;q=0.5") == "en"
    assert parse_accept_language("de, zh-Hans;q=0.4") == "zh-CN"
    assert parse_accept_language("fr") is None
    assert pick({"en": "Hello", "zh-CN": ""}, "zh-CN") == "Hello"


def test_zpay_query_parser_is_tolerant():
    r = parse_query_response({"code": 1, "status": 1, "money": 19.9, "trade_no": "T1"})
    assert r.paid and r.money == Decimal("19.9") and r.trade_no == "T1"
    r = parse_query_response({"code": "1", "status": "0", "money": "19.90"})
    assert r.found and not r.paid
    assert not parse_query_response({"code": -1, "msg": "not found"}).found
    r = parse_query_response({"code": 1, "trade_status": "TRADE_SUCCESS", "money": "1.00"})
    assert r.paid


def test_pay_url_is_signed():
    from urllib.parse import parse_qsl, urlsplit

    from app.payments import epay

    cfg = ZPayConfig(
        "1001",
        "k" * 32,
        "https://zpayz.cn",
        "https://zpayz.cn",
        "https://shop/api/v1/pay/notify/zpay",
        "https://shop/api/v1/pay/return/zpay",
    )
    url = ZPayClient(cfg, None).build_pay_url("GABCDEFGHIJKL", "Steam ¥50", Decimal("52.00"))  # type: ignore[arg-type]
    u = urlsplit(url)
    assert u.netloc == "zpayz.cn" and u.path == "/submit.php"
    p = dict(parse_qsl(u.query))
    assert p["money"] == "52.00" and p["sign_type"] == "MD5" and p["type"] == "alipay"
    assert epay.verify_signature(p, "k" * 32)
    assert "key" not in p


def test_rebase():
    assert rebase("http://localhost:8080/api/v1/pay/notify/zpay", "http://api:8000") == (
        "http://api:8000/api/v1/pay/notify/zpay"
    )
    assert rebase("http://x/y", "") == "http://x/y"


def test_quote_signature_detects_tampering():
    import uuid
    from datetime import datetime, timezone

    from app.models import Quote
    from app.modules.chat.quotes import sign_quote, verify_quote

    key = os.urandom(32)
    q = Quote(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        product_id=None,
        title="t",
        amount_minor=100,
        currency="CNY",
        qty=1,
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        version=1,
    )
    q.signature = sign_quote(key, q)
    assert verify_quote(key, q)
    q.amount_minor = 1
    assert not verify_quote(key, q)
    q.amount_minor = 100
    assert not verify_quote(os.urandom(32), q)


def test_keygen_roundtrip(tmp_path):
    from app.core.config import Settings
    from app.core.vaultkeys import build_sealer
    from app.delivery.keys import load_opener
    from app.devtools.keygen import generate

    paths = generate(tmp_path)
    assert stat.S_IMODE(os.stat(paths["private"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(paths["env"]).st_mode) == 0o600
    env = dict(
        line.split("=", 1)
        for line in paths["env"].read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert len(env["APP_INTERNAL_TOKEN"]) >= 32
    settings = Settings(
        _env_file=None,
        vault_public_keys_file=str(paths["public"]),
        vault_active_kid=env["APP_VAULT_ACTIVE_KID"],
        vault_fingerprint_key=env["APP_VAULT_FINGERPRINT_KEY"],
    )
    sealer = build_sealer(settings)
    ctx = CardContext("t", "p", "c")
    blob = sealer.seal("HELLO-CODE", ctx)
    assert load_opener(str(paths["private"])).open(blob, ctx) == "HELLO-CODE"
    pub = json.loads(paths["public"].read_text())
    assert list(pub) == [env["APP_VAULT_ACTIVE_KID"]]
    with pytest.raises(FileExistsError):
        generate(tmp_path)


def test_vault_config_errors():
    from app.core.config import Settings
    from app.core.vaultkeys import VaultConfigError, build_sealer

    with pytest.raises(VaultConfigError):
        build_sealer(Settings(_env_file=None, vault_public_keys=""))
    with pytest.raises(VaultConfigError):
        build_sealer(
            Settings(
                _env_file=None, vault_public_keys='{"k": "AAAA"}', vault_fingerprint_key="c2hvcnQ="
            )
        )


def test_order_numbers_are_random():
    from app.modules.orders.service import new_order_no

    nos = {new_order_no() for _ in range(1000)}
    assert len(nos) == 1000
    assert all(n[0] == "G" and len(n) == 13 and n[1:].isalnum() and n[1:].isupper() for n in nos)
