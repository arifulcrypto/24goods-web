"""#58 §6: unpaid-order cap default 2 and order-creation throttles (per account, per IP)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.settings_v5 import DEFAULTS


def test_new_defaults():
    assert Settings.model_fields["max_pending_orders"].default == 2
    assert DEFAULTS["defaults"]["max_pending_orders"] == 2
    f = Settings.model_fields
    assert f["order_create_per_user"].default == 3 and f["order_create_user_window_s"].default == 60
    assert f["order_create_per_ip"].default == 10 and f["order_create_ip_window_s"].default == 300


def _tune(env, **over):
    env.res.settings = env.res.settings.model_copy(update=over)


async def _buy(client, p):
    return await client.post("/api/v1/orders", json={"product_id": str(p.id), "qty": 1})


async def test_per_account_flood_is_refused_and_audited_once(env):
    _tune(env, order_create_per_user=2, max_pending_orders=10)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, bu = await env.login("buyer")
    assert (await _buy(buyer, p)).status_code == 200
    assert (await _buy(buyer, p)).status_code == 200
    r = await _buy(buyer, p)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert (await _buy(buyer, p)).status_code == 429
    n = await env.scalar(
        "SELECT count(*) FROM audit_log WHERE action='risk.order_flood_detected' "
        "AND severity='high' AND actor_id=:u",
        u=bu.id,
    )
    assert n == 1  # one signal per window, not one per refused request
    # another account is not affected by this account's limit
    other, _ = await env.login("buyer")
    assert (await _buy(other, p)).status_code == 200


async def test_per_ip_limit_spans_accounts(env):
    _tune(env, order_create_per_ip=2, max_pending_orders=10)
    p = await env.product(mode="manual", price=1000, floor=100)
    codes = []
    for _ in range(3):
        c, _u = await env.login("buyer")  # same client address, fresh account each time
        codes.append((await _buy(c, p)).status_code)
    assert codes == [200, 200, 429]
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='risk.order_flood_detected' "
            "AND detail->>'scope' = 'ip'"
        )
        == 1
    )
