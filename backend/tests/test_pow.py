"""Self-hosted proof of work: format, single use, expiry, scope, difficulty."""

from __future__ import annotations

import hashlib
import time

from app.core import pow as powmod
from tests.helpers import solve_pow


async def challenge(c, scope="instant") -> dict:
    r = await c.get("/api/v1/pow/challenge", params={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


def good_nonce(ch: dict) -> str:
    return powmod.solve(ch["challenge"], ch["bits"])


def bad_nonce(ch: dict) -> str:
    n = 0
    while True:
        d = hashlib.sha256(f"{ch['challenge']}:{n}".encode()).digest()
        if powmod.leading_zero_bits(d) < ch["bits"]:
            return str(n)
        n += 1


async def test_challenge_shape_and_scope_validation(env):
    c = env.client()
    ch = await challenge(c, "lookup")
    assert ch["bits"] == 8 and ch["expires_at"] > time.time()
    assert ch["challenge"].startswith("v1.lookup.8.")
    r = await c.get("/api/v1/pow/challenge", params={"scope": "nope"})
    assert r.status_code == 422
    assert (await c.get("/api/v1/pow/challenge")).status_code == 422


async def test_valid_solution_accepted_then_replay_rejected(env):
    c = env.client()
    ch = await challenge(c)
    pow_in = {"challenge": ch["challenge"], "nonce": good_nonce(ch)}
    r = await c.post("/api/v1/auth/instant", json={"pow": pow_in})
    assert r.status_code == 200, r.text
    r = await c.post("/api/v1/auth/instant", json={"pow": pow_in})  # single use
    assert r.status_code == 422 and r.json()["error"]["code"] == "pow_invalid"


async def test_missing_pow_is_pow_required(env):
    c = env.client()
    r = await c.post("/api/v1/auth/instant", json={})
    assert r.status_code == 428 and r.json()["error"]["code"] == "pow_required"


async def test_wrong_nonce_does_not_burn_the_challenge(env):
    c = env.client()
    ch = await challenge(c)
    r = await c.post(
        "/api/v1/auth/instant", json={"pow": {"challenge": ch["challenge"], "nonce": bad_nonce(ch)}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "pow_invalid"
    r = await c.post(
        "/api/v1/auth/instant",
        json={"pow": {"challenge": ch["challenge"], "nonce": good_nonce(ch)}},
    )
    assert r.status_code == 200


async def test_wrong_scope_tampered_and_garbage_rejected(env):
    c = env.client()
    ch = await challenge(c, "lookup")
    nonce = good_nonce(ch)
    r = await c.post(
        "/api/v1/auth/instant", json={"pow": {"challenge": ch["challenge"], "nonce": nonce}}
    )
    assert r.json()["error"]["code"] == "pow_invalid"  # lookup challenge used for instant
    easy = ch["challenge"].replace(".lookup.8.", ".instant.8.")  # MAC no longer matches
    r = await c.post("/api/v1/auth/instant", json={"pow": {"challenge": easy, "nonce": "0"}})
    assert r.json()["error"]["code"] == "pow_invalid"
    for junk in ("x", "v1.instant.0.1.2.3", ch["challenge"] + "0"):
        r = await c.post("/api/v1/auth/instant", json={"pow": {"challenge": junk, "nonce": "1"}})
        assert r.json()["error"]["code"] == "pow_invalid"
    r = await c.post(
        "/api/v1/auth/instant", json={"pow": {"challenge": ch["challenge"], "nonce": "-1"}}
    )
    assert r.json()["error"]["code"] == "pow_invalid"


async def test_challenge_with_fewer_bits_than_configured_is_refused(env):
    c = env.client()
    easy = powmod.issue(env.res.settings.model_copy(update={"pow_bits": 1}), "instant")
    nonce = powmod.solve(easy["challenge"], 1)
    r = await c.post(
        "/api/v1/auth/instant", json={"pow": {"challenge": easy["challenge"], "nonce": nonce}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "pow_invalid"


async def test_expired_challenge_rejected(env):
    c = env.client()
    old = powmod.issue(env.res.settings, "instant", now=time.time() - 1000)
    nonce = powmod.solve(old["challenge"], old["bits"])
    r = await c.post(
        "/api/v1/auth/instant", json={"pow": {"challenge": old["challenge"], "nonce": nonce}}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "pow_invalid"


async def test_leading_zero_bits_arithmetic():
    assert powmod.leading_zero_bits(b"\x00\x00\x01") == 23
    assert powmod.leading_zero_bits(b"\x80") == 0
    assert powmod.leading_zero_bits(b"\x0f\xff") == 4


async def test_solve_helper_roundtrip(env):
    c = env.client()
    pow_in = await solve_pow(c, "register")
    assert pow_in["challenge"].startswith("v1.register.")
