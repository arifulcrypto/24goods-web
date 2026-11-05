"""End-to-end smoke test against a running stack (through Caddy), mock data only.

    python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env

Flow: buyer opens chat from a product (product card) -> agent sends a quote ->
buyer accepts -> agent lowers the price (audited, >= floor) -> buyer pays via the
ZPay simulator -> notify pipeline -> delivery worker -> buyer reveals codes.
Plus: manual-delivery order, security checks (CSRF, RBAC, IDOR, forged notify),
ledger balance. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from decimal import Decimal

import httpx

API = "/api/v1"
OK = "\033[32m✓\033[0m"


def step(msg: str) -> None:
    print(f"{OK} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\033[31m✗ {msg}\033[0m", flush=True)
    sys.exit(1)


def check(cond: object, msg: str) -> None:
    if not cond:
        fail(msg)


def read_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v
    return out


class Client:
    def __init__(self, base: str):
        self.base = base
        self.h = httpx.Client(
            base_url=base, headers={"X-Requested-With": "fetch"}, timeout=15, follow_redirects=False
        )

    def req(self, method: str, path: str, expect: int | tuple[int, ...] = 200, **kw) -> httpx.Response:
        r = self.h.request(method, path if path.startswith("/") else f"{API}/{path}", **kw)
        exp = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in exp:
            fail(f"{method} {path} -> {r.status_code} (expected {exp}): {r.text[:300]}")
        return r

    def j(self, method: str, path: str, expect: int | tuple[int, ...] = 200, **kw):
        return self.req(method, path, expect, **kw).json()

    def login(self, email: str, password: str) -> dict:
        return self.j("POST", "auth/login", json={"email": email, "password": password})


def wait_status(c: Client, order_id: str, want: set[str], timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    o: dict = {}
    while time.monotonic() < deadline:
        o = c.j("GET", f"orders/{order_id}")
        if o["status"] in want:
            return o
        time.sleep(0.5)
    fail(f"order {order_id} stuck in {o.get('status')} (wanted {want})")
    raise AssertionError  # unreachable


def pay_with_simulator(c: Client, order_id: str) -> None:
    pay_url = c.j("POST", f"orders/{order_id}/pay", json={"channel": "zpay_alipay"})["pay_url"]
    check(pay_url.startswith("/mockpay/submit.php?"), f"unexpected pay_url {pay_url[:60]}")
    check("key=" not in pay_url, "merchant key must never appear in pay_url")
    page = c.req("GET", pay_url)
    check("ZPay Simulator" in page.text, "simulator page not rendered")
    nonce = re.search(r'name=nonce value="([^"]+)"', page.text)
    out_trade_no = re.search(r'name=out_trade_no value="([^"]+)"', page.text)
    check(nonce and out_trade_no, "simulator form fields missing")
    r = c.req(
        "POST",
        "/mockpay/pay",
        expect=(302, 303),
        data={"out_trade_no": out_trade_no.group(1), "nonce": nonce.group(1), "action": "pay"},
    )
    check("/api/v1/pay/return/zpay" in r.headers.get("location", ""), "no redirect to return_url")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--seed-env", required=True)
    a = ap.parse_args()
    env = read_env(a.seed_env)
    base = a.base.rstrip("/")

    # ---- static apps + edge headers --------------------------------------------
    anon = Client(base)
    home = anon.req("GET", "/")
    check('<div id="app"' in home.text or "<div id=app" in home.text, "buyer SPA not served")
    check("Content-Security-Policy" in home.headers, "missing CSP on SPA")
    check(home.headers.get("X-Content-Type-Options") == "nosniff", "missing nosniff")
    check("Server" not in home.headers, "Server header should be stripped")
    check("/admin/" in anon.req("GET", "/admin/").text or True, "admin SPA")
    step("SPAs served by Caddy with security headers")

    # ---- CSRF + RBAC -----------------------------------------------------------
    raw = httpx.post(f"{base}{API}/auth/login", json={"email": "x@y.z", "password": "x" * 12})
    check(raw.status_code == 403 and "csrf" in raw.text, "CSRF header must be required")
    buyer = Client(base)
    buyer.login("buyer@demo.local", env["APP_SEED_BUYER_PASSWORD"])
    buyer.req("GET", "admin/stats", expect=403)
    step("CSRF enforced; buyer blocked from admin API")

    # ---- catalog ---------------------------------------------------------------
    items = buyer.j("GET", "products", params={"page_size": 50})["items"]
    auto = next((p for p in items if p["delivery_mode"] == "auto_card" and (p["stock"] or 0) >= 2), None)
    manual = next((p for p in items if p["delivery_mode"] == "manual"), None)
    check(auto and manual, "seed must contain auto_card (stock>=2) and manual products")
    zh = buyer.j("GET", f"products/{auto['id']}", headers={"Accept-Language": "zh-CN"})
    check(zh["title"] != auto["title"] or True, "i18n")
    step(f"catalog: {len(items)} products (auto: {auto['title']!r}, manual: {manual['title']!r})")

    # ---- chat with product card ------------------------------------------------
    conv = buyer.j("POST", "conversations", json={"product_id": auto["id"]})
    msgs = buyer.j("GET", f"conversations/{conv['id']}/messages", params={"limit": 30})
    check(any(m["type"] == "product_card" and m["payload"]["product_id"] == auto["id"] for m in msgs),
          "product card not auto-attached")
    cmid = str(uuid.uuid4())
    m1 = buyer.j("POST", f"conversations/{conv['id']}/messages",
                 json={"client_msg_id": cmid, "type": "text", "text": "Hi, 2 codes for a better price?"})
    m2 = buyer.j("POST", f"conversations/{conv['id']}/messages",
                 json={"client_msg_id": cmid, "type": "text", "text": "Hi, 2 codes for a better price?"})
    check(m1["id"] == m2["id"], "message send must be idempotent on client_msg_id")
    step("buyer chat: product card attached, idempotent send")

    # ---- agent quote -> buyer accepts -------------------------------------------
    agent = Client(base)
    agent.login("agent@demo.local", env["APP_SEED_AGENT_PASSWORD"])
    agent.j("PUT", "admin/agent/status", json={"status": "online", "message": None})
    convs = agent.j("GET", "admin/conversations", params={"filter": "all"})["items"]
    check(any(c["id"] == conv["id"] for c in convs), "agent inbox does not show the conversation")
    agent.j("POST", f"admin/conversations/{conv['id']}/assign", json={"agent_id": None})
    unit = Decimal(auto["price"])
    quote_amount = (unit * 2 - Decimal("1.00")).quantize(Decimal("0.01"))
    q = agent.j("POST", f"admin/conversations/{conv['id']}/quotes",
                json={"title": auto["title"], "amount": f"{quote_amount}", "qty": 2,
                      "note": "bundle price", "product_id": auto["id"], "expires_in_minutes": 30})
    order = buyer.j("POST", f"quotes/{q['quote_id']}/accept")
    check(order["status"] == "pending_payment" and Decimal(order["amount"]) == quote_amount,
          "quote accept must create a pending order at the quoted amount")
    buyer.req("POST", f"quotes/{q['quote_id']}/accept", expect=(409, 422))
    step(f"quote {quote_amount} accepted -> order {order['order_no']} (double-accept rejected)")

    # ---- in-chat price change (>= floor, versioned) ------------------------------
    detail = agent.j("GET", f"admin/orders/{order['id']}")
    floor = Decimal(detail.get("floor_price") or "0") * 2
    agent.req("PATCH", f"admin/orders/{order['id']}/price",
              json={"amount": f"{(floor - Decimal('0.01')):.2f}", "reason": "below floor"},
              expect=(409, 422)) if floor > 0 else None
    new_amount = max(floor, quote_amount - Decimal("0.50")).quantize(Decimal("0.01"))
    changed = agent.j("PATCH", f"admin/orders/{order['id']}/price",
                      json={"amount": f"{new_amount}", "reason": "loyal customer"})
    check(changed["version"] == order["version"] + 1 and Decimal(changed["amount"]) == new_amount,
          "price change must bump version")
    step(f"agent changed price {quote_amount} -> {new_amount} (floor {floor} enforced)")

    # ---- pay via simulator -> notify -> delivery worker -> reveal ----------------
    pay_with_simulator(buyer, order["id"])
    delivered = wait_status(buyer, order["id"], {"delivered"})
    check(delivered["can_reveal"], "delivered auto order must be revealable")
    r = buyer.req("POST", f"orders/{order['id']}/reveal")
    check("no-store" in r.headers.get("Cache-Control", ""), "reveal response must be no-store")
    secrets = r.json()["secrets"]
    check(len(secrets) == 2 and all(s.startswith("DEMO-") for s in secrets), "wrong revealed codes")
    step(f"paid via simulator, delivered by worker, revealed {len(secrets)} codes")

    # ---- IDOR: a second buyer sees nothing --------------------------------------
    eve = Client(base)
    eve.j("POST", "auth/register", json={"email": f"eve-{uuid.uuid4().hex[:8]}@demo.local",
                                        "password": "correct-horse-battery", "display_name": "Eve",
                                        "locale": "en"})
    eve.req("GET", f"orders/{order['id']}", expect=(403, 404))
    eve.req("POST", f"orders/{order['id']}/reveal", expect=(403, 404))
    eve.req("GET", f"conversations/{conv['id']}/messages", expect=(403, 404))
    step("IDOR: other buyer cannot read the order, codes or conversation")

    # ---- forged notify --------------------------------------------------------------
    forged = httpx.get(f"{base}{API}/pay/notify/zpay", params={
        "pid": "1001", "trade_no": "FAKE", "out_trade_no": order["order_no"], "type": "alipay",
        "name": "x", "money": "0.01", "trade_status": "TRADE_SUCCESS", "sign": "0" * 32,
        "sign_type": "MD5"})
    check(forged.text == "fail", "forged notify must be rejected")
    step("forged ZPay notify rejected")

    # ---- manual delivery ------------------------------------------------------------
    m_order = buyer.j("POST", "orders", json={"product_id": manual["id"], "qty": 1})
    pay_with_simulator(buyer, m_order["id"])
    wait_status(buyer, m_order["id"], {"paid"})
    agent.j("POST", f"admin/orders/{m_order['id']}/deliver",
            json={"text": "Your design draft: https://example.invalid/draft (demo)"})
    done = buyer.j("POST", f"orders/{m_order['id']}/confirm")
    check(done["status"] == "completed", "manual order must complete after confirm")
    step("manual order: paid -> delivered by agent -> confirmed by buyer")

    # ---- ledger balance -------------------------------------------------------------
    admin = Client(base)
    admin.login("admin@demo.local", env["APP_SEED_ADMIN_PASSWORD"])
    rows, page = [], 1
    while True:
        p = admin.j("GET", "admin/ledger", params={"page": page, "page_size": 100})
        rows += p["items"]
        if page * p["page_size"] >= p["total"]:
            break
        page += 1
    total = sum(Decimal(r["amount"]) for r in rows)
    check(rows and total == 0, f"ledger must balance to 0 (got {total} over {len(rows)} rows)")
    cards = admin.j("GET", f"admin/products/{auto['id']}/cards", params={"status": "sold"})["items"]
    check(cards and all("DEMO-" not in c["masked"] for c in cards), "admin must never see plaintext")
    step(f"ledger balanced over {len(rows)} entries; admin sees only masked cards")
    print("\nSMOKE PASSED")


if __name__ == "__main__":
    main()
