from decimal import Decimal

import pytest

from app.payments.epay import (
    ExpectedOrder,
    NotifyRejected,
    canonical_string,
    sign,
    verify_notify,
    verify_signature,
)

KEY = "merchant-secret"
EXPECTED = ExpectedOrder(out_trade_no="O100", merchant_id="1001", amount=Decimal("19.90"))


def notify(**over):
    p = {
        "pid": "1001",
        "trade_no": "T999",
        "out_trade_no": "O100",
        "type": "alipay",
        "name": "Gift card",
        "money": "19.90",
        "trade_status": "TRADE_SUCCESS",
        "sign_type": "MD5",
    }
    p.update(over)
    p["sign"] = sign(p, KEY)
    return p


def test_canonical_excludes_sign_and_empty():
    s = canonical_string({"b": "2", "a": "1", "sign": "x", "sign_type": "MD5", "c": ""})
    assert s == "a=1&b=2"


def test_valid_notify():
    assert verify_notify(notify(), KEY, EXPECTED) == "T999"


def test_forged_signature():
    p = notify()
    p["money"] = "0.01"  # tamper after signing
    assert not verify_signature(p, KEY)
    with pytest.raises(NotifyRejected, match="bad_signature"):
        verify_notify(p, KEY, EXPECTED)


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"money": "0.01"}, "amount_mismatch"),  # correctly signed by a different order
        ({"pid": "2002"}, "merchant_mismatch"),
        ({"out_trade_no": "O101"}, "order_mismatch"),
        ({"trade_status": "WAIT"}, "not_paid"),
        ({"money": "abc"}, "bad_amount"),
    ],
)
def test_rejections(over, reason):
    with pytest.raises(NotifyRejected, match=reason):
        verify_notify(notify(**over), KEY, EXPECTED)


def test_empty_key_never_verifies():
    p = notify()
    assert not verify_signature(p, "")


def test_sign_type_is_pinned():
    p = notify(sign_type="RSA")
    assert not verify_signature(p, KEY)
