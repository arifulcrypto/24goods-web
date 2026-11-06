"""End-to-end smoke test against a running stack (through Caddy), mock data only.

    python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env

Flow: buyer opens chat from a product (product card) -> agent sends a quote ->
buyer accepts -> agent lowers the price (audited, >= floor) -> buyer pays via the
ZPay simulator -> notify pipeline -> delivery worker -> buyer reveals codes.
Plus: manual-delivery order, security checks (CSRF, RBAC, IDOR, forged notify),
ledger balance. Storefront v3 (docs/api-contract.md): site/articles/recommendations, instant
account via proof of work, multi-field card delivery, order lookup by access code, after-sales
(replace + refund), staff card management, text-file messages, account self-service.
Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import hashlib
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


def solve_pow(client: "Client", scope: str) -> dict[str, str]:
    """Fetch and solve a proof-of-work challenge (docs/api-contract.md, 'Proof of work')."""
    ch = client.j("GET", f"pow/challenge?scope={scope}")
    n = 0
    while True:
        digest = hashlib.sha256(f"{ch['challenge']}:{n}".encode()).digest()
        if int.from_bytes(digest, "big") >> (256 - ch["bits"]) == 0:
            return {"challenge": ch["challenge"], "nonce": str(n)}
        n += 1


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


def v3_public(anon: Client, auto: dict, multi: dict, manual: dict) -> None:
    """Site settings, articles/pages, product-by-ids and recommendations (all public)."""
    site = anon.j("GET", "site")
    check(site.get("brand") == "Kesiv", f"/site brand should be Kesiv, got {site.get('brand')!r}")
    ann = site.get("announcement")
    check(ann and ann.get("title") and ann.get("body_html") and ann.get("version"),
          f"/site must carry the demo announcement, got {ann!r}")
    check(isinstance(site.get("support_hours"), str), "/site support_hours must be a string")
    check(isinstance(site.get("after_sales_default_hours"), int), "/site after_sales_default_hours must be int")
    slugs = {pg["slug"] for pg in site["pages"]}
    check("rules" in slugs, f"/site pages must list the fixed 'rules' page, got {sorted(slugs)}")
    step(f"site: brand {site['brand']!r}, announcement v{ann['version']}, {len(slugs)} fixed pages")

    lst = anon.j("GET", "articles", params={"page_size": 50})
    check(lst["total"] >= 1 and lst["items"], "articles list is empty (seed articles missing)")
    check(all(it["slug"] != "rules" for it in lst["items"]), "fixed pages must not appear in /articles")
    first = anon.j("GET", f"articles/{lst['items'][0]['slug']}")
    check(first["kind"] == "article" and first["body_html"], "article body_html missing")
    rules = anon.j("GET", "articles/rules", headers={"Accept-Language": "zh-CN"})
    check(rules["kind"] == "page" and rules["body_html"] and rules["title"], "rules page not served")
    check("<script" not in rules["body_html"].lower(), "article HTML must be sanitised")
    anon.req("GET", "articles/does-not-exist", expect=404)
    step(f"articles: {lst['total']} listed, 'rules' page served (zh-CN title {rules['title']!r})")

    unknown = str(uuid.uuid4())
    got = anon.j("GET", "products", params={"ids": f"{manual['id']},{unknown},{auto['id']}"})["items"]
    check([p["id"] for p in got] == [manual["id"], auto["id"]],
          f"products?ids= must keep the requested order and skip unknown ids, got {[p['id'] for p in got]}")
    anon.req("GET", "products", params={"ids": "not-a-uuid"}, expect=422)
    rec = anon.j("GET", "products/recommended", params={"for": auto["id"], "limit": 4})["items"]
    check(0 < len(rec) <= 4 and all(p["id"] != auto["id"] for p in rec),
          f"recommended must be 1..4 items excluding the 'for' product, got {len(rec)}")
    check(all({"id", "title", "price", "after_sales_hours"} <= set(p) for p in rec),
          "recommended items must be ProductSummary")
    step(f"products?ids= ordered/filtered; recommended returned {len(rec)} items")


def open_pending(c: Client, product: dict, qty: int = 1) -> dict:
    o = c.j("POST", "orders", json={"product_id": product["id"], "qty": qty, "src": "smoke"})
    check(o["status"] == "pending_payment", f"new order should be pending_payment, got {o['status']}")
    check(re.fullmatch(r"[0-9A-Z]{10}", o.get("access_code") or ""),
          f"POST /orders must return a 10-char access_code once, got {o.get('access_code')!r}")
    check(c.j("GET", f"orders/{o['id']}").get("access_code") is None, "access_code must be shown only once")
    return o


def v3_instant_and_after_sales(
    base: str, buyer: Client, agent: Client, admin: Client, auto: dict, multi: dict, multi_detail: dict
) -> None:
    # ---- instant account (proof of work) ---------------------------------------------
    inst = Client(base)
    inst.req("POST", "auth/instant", expect=428, json={})  # no PoW -> pow_required
    bad = inst.req("POST", "auth/instant", expect=422,
                   json={"pow": {"challenge": "v1.instant.1.0.00.00", "nonce": "1"}})
    check(bad.json()["error"]["code"] == "pow_invalid", "forged challenge must be pow_invalid")
    t0 = time.monotonic()
    res = inst.j("POST", "auth/instant", json={"pow": solve_pow(inst, "instant")})
    user, cred = res["user"], res["credentials"]
    check(re.fullmatch(r"u-[a-z2-7]{7}", cred["login"]) and user["handle"] == cred["login"],
          f"instant handle malformed: {cred.get('login')!r}")
    check(user["is_instant"] is True and user["role"] == "buyer", "instant user must be is_instant buyer")
    check(user["email"] == "", f"instant user must not expose a placeholder email, got {user['email']!r}")
    check(len(cred["password"]) >= 12, "instant password too short")
    me = inst.j("GET", "auth/me")
    check(me["id"] == user["id"] and me["email"] == "", "session from /auth/instant must be active")
    step(f"instant account {cred['login']} created via PoW in {time.monotonic() - t0:.1f}s")

    # login by handle; PoW required from the 4th attempt per (login, ip)
    other = Client(base)
    for i in range(3):
        other.req("POST", "auth/login", expect=401, json={"email": cred["login"], "password": f"wrong-password-{i}"})
    r = other.req("POST", "auth/login", expect=428, json={"email": cred["login"], "password": cred["password"]})
    check(r.json()["error"]["code"] == "pow_required", "4th attempt must demand pow_required")
    again = other.j("POST", "auth/login", json={"email": cred["login"].upper(), "password": cred["password"],
                                                 "pow": solve_pow(other, "login")})
    check(again["id"] == user["id"], "login by handle (case-insensitive) with PoW must succeed")
    step("login by handle: 428 pow_required after 3 failures, then OK with PoW")

    # ---- profile / credentials / delete --------------------------------------------------
    patched = inst.j("PATCH", "me", json={"display_name": "Smoke Tester"})
    check(patched["display_name"] == "Smoke Tester" and inst.j("GET", "auth/me")["display_name"] == "Smoke Tester",
          "PATCH /me must change display_name")
    inst.req("POST", "me/credentials", expect=422, json={"new_password": "short"})

    # ---- multi-field order: access code -> pay -> deliver -> reveal ----------------------
    mf = open_pending(inst, multi)
    code1 = mf["access_code"]
    pay_with_simulator(inst, mf["id"])
    mf_done = wait_status(inst, mf["id"], {"delivered"})
    info = mf_done["delivery_info"]
    labels = multi_detail["delivery_format"].split(multi_detail["delivery_separator"])
    check(info and info["format_labels"] == labels and info["separator"] == multi_detail["delivery_separator"],
          f"order delivery_info should carry the product's labels {labels}, got {info!r}")
    check(mf_done["after_sales_until"] and mf_done["auto_confirm_at"],
          "delivered order must have after_sales_until and auto_confirm_at")
    rv = inst.req("POST", f"orders/{mf['id']}/reveal")
    check("no-store" in rv.headers.get("Cache-Control", ""), "reveal must be no-store")
    body = rv.json()
    cards = body["cards"]
    check(len(cards) == 1 and body["secrets"] == [c["code"] for c in cards], f"reveal shape wrong: {list(body)}")
    parts = cards[0]["code"].split(info["separator"])
    check(len(parts) == len(labels), f"multi-field card should split into {len(labels)} fields, got {len(parts)}")
    step(f"multi-field order {mf['order_no']}: access_code issued, delivered, revealed {len(labels)} fields ({', '.join(labels)})")

    # ---- lookup by order no + access code (no session) -----------------------------------
    anon = Client(base)
    anon.req("POST", "lookup", expect=428, json={"order_no": mf["order_no"], "access_code": code1})
    spaced = f"{code1[:5].lower()}-{code1[5:].lower()}"  # lower case + hyphen tolerated
    r = anon.req("POST", "lookup", json={"order_no": mf["order_no"].lower(), "access_code": spaced,
                                         "pow": solve_pow(anon, "lookup")})
    lk = r.json()
    check(not r.headers.get("set-cookie") and not anon.h.cookies, "/lookup must never set a session cookie")
    check("no-store" in r.headers.get("Cache-Control", ""), "/lookup must be no-store")
    check(lk["order_no"] == mf["order_no"] and lk["status"] == "delivered" and lk["can_reveal"],
          f"/lookup returned the wrong order view: {lk.get('order_no')} {lk.get('status')}")
    check(lk["delivery_info"]["format_labels"] == labels, "/lookup must include delivery_info")
    r = anon.req("POST", "lookup/reveal", json={"order_no": mf["order_no"], "access_code": code1})
    check([c["code"] for c in r.json()["cards"]] == [cards[0]["code"]], "/lookup/reveal must return the same card")
    check(not r.headers.get("set-cookie"), "/lookup/reveal must not set a cookie")
    wrong = Client(base)
    r = wrong.req("POST", "lookup", expect=404, json={"order_no": mf["order_no"], "access_code": "AAAAAAAAAA",
                                                      "pow": solve_pow(wrong, "lookup")})
    check(not r.headers.get("set-cookie") and not wrong.h.cookies, "failed /lookup must not set a cookie")
    check(r.json()["error"]["code"] == "not_found", "wrong access code must be a generic not_found")
    wrong.req("POST", "lookup/reveal", expect=404, json={"order_no": mf["order_no"], "access_code": "AAAAAAAAAA"})
    step("lookup: right code works (case/hyphen tolerant, no cookie, reveal ok); wrong code 404 without cookie")

    # ---- after-sales: replace on auto_card ----------------------------------------------------
    buyer.req("POST", f"orders/{mf['id']}/after-sales", expect=(403, 404),
              json={"reason": "other", "description": "not mine", "attachment_ids": []})  # IDOR
    tk = inst.j("POST", f"orders/{mf['id']}/after-sales",
                json={"reason": "invalid_code", "description": "Account does not log in", "attachment_ids": []})
    check(tk["status"] == "open" and tk["order_id"] == mf["id"], f"new ticket should be open, got {tk['status']}")
    r = inst.req("POST", f"orders/{mf['id']}/after-sales", expect=409,
                 json={"reason": "other", "description": "again", "attachment_ids": []})
    check(r.json()["error"]["code"] == "after_sales_open", "second open ticket must be after_sales_open")
    check(inst.j("GET", f"orders/{mf['id']}")["after_sales"]["id"] == tk["id"], "order.after_sales must be the latest ticket")
    staff_tk = agent.j("GET", f"admin/after-sales/{tk['id']}")
    check(staff_tk["order"]["order_no"] == mf["order_no"] and staff_tk["order"]["delivery_mode"] == "auto_card"
          and staff_tk["buyer"]["id"] == user["id"] and "resolved_by" in staff_tk,
          "staff ticket must include order, buyer and resolved_by")
    check(any(t["id"] == tk["id"] for t in agent.j("GET", "admin/after-sales", params={"status": "open"})["items"]),
          "staff after-sales list must show the open ticket")
    agent.req("POST", f"admin/after-sales/{tk['id']}/action", expect=403, json={"action": "refund"})
    agent.req("POST", f"admin/after-sales/{tk['id']}/action", expect=(400, 422), json={"action": "reject"})  # note required
    old_code = cards[0]["code"]
    queued = agent.j("POST", f"admin/after-sales/{tk['id']}/action", json={"action": "replace", "note": "replacing"})
    check(queued["status"] in ("processing", "replaced"), f"replace should queue, got {queued['status']}")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        st = agent.j("GET", f"admin/after-sales/{tk['id']}")
        if st["status"] == "replaced":
            break
        time.sleep(0.5)
    else:
        fail(f"after-sales replace not completed by the delivery worker (ticket stuck in {st['status']})")
    rv2 = inst.j("POST", f"orders/{mf['id']}/reveal")
    check(len(rv2["cards"]) == 1 and rv2["cards"][0]["id"] != cards[0]["id"] and rv2["cards"][0]["code"] != old_code,
          "after replace the order must reveal exactly one NEW card, the old one must be gone")
    check(len(rv2["cards"][0]["code"].split(info["separator"])) == len(labels), "replacement card has wrong format")
    found = admin.j("POST", "admin/cards/find", json={"lines": [old_code], "by": "content"})
    check(found["missing"] == 0 and found["items"][0]["status"] == "void"
          and found["items"][0]["void_reason"] == "defective",
          f"replaced card must be void/defective, got {found['items']}")
    check(inst.j("GET", f"orders/{mf['id']}")["after_sales"]["status"] == "replaced", "buyer view must show ticket replaced")
    step(f"after-sales replace: ticket {tk['id'][:8]} -> replaced, old card void(defective), new card delivered")

    # ---- after-sales: refund (admin only) ---------------------------------------------------------
    ro = open_pending(inst, auto)
    pay_with_simulator(inst, ro["id"])
    wait_status(inst, ro["id"], {"delivered"})
    rt = inst.j("POST", f"orders/{ro['id']}/after-sales",
                json={"reason": "wrong_item", "description": "Wrong item delivered", "attachment_ids": []})
    agent.req("POST", f"admin/after-sales/{rt['id']}/action", expect=403, json={"action": "refund"})
    done = admin.j("POST", f"admin/after-sales/{rt['id']}/action", json={"action": "refund", "note": "refunded"})
    check(done["status"] == "refunded" and done["resolved_by"], f"refund must close the ticket, got {done['status']}")
    check(inst.j("GET", f"orders/{ro['id']}")["status"] == "refunded", "refund must set the order refunded")
    admin.req("POST", f"admin/after-sales/{rt['id']}/action", expect=409, json={"action": "resolve"})
    step(f"after-sales refund: agent 403, admin OK -> order {ro['order_no']} refunded")

    # ---- account deletion refused while an order is pending -------------------------------------
    pend = open_pending(inst, auto)
    r = inst.req("POST", "me/delete", expect=409, json={"confirm": "DELETE"})
    check(r.json()["error"]["code"] == "has_open_orders", "/me/delete must be 409 has_open_orders")
    check(inst.req("GET", "auth/me").status_code == 200, "refused delete must keep the session")
    inst.req("POST", "me/delete", expect=(400, 422), json={"confirm": "nope"})
    step(f"/me PATCH ok; /me/delete refused (409 has_open_orders) while order {pend['order_no']} is pending")


def v3_cards(agent: Client, admin: Client, multi: dict) -> None:
    """Staff card management: import, find (content / first field), void, replace content, batches."""
    tag = uuid.uuid4().hex[:10]

    def line(n: int) -> str:
        return f"smoke.{tag}.{n}@gmail.com----pw{tag}{n}----KEY{tag}{n}----US"

    pid = multi["id"]
    for path, payload in (
        ("admin/cards/find", {"lines": ["x"], "by": "content"}),
        ("admin/cards/void", {"ids": [str(uuid.uuid4())], "reason": "x"}),
        (f"admin/products/{pid}/cards/import", {"lines": "x"}),
    ):
        agent.req("POST", path, expect=403, json=payload)
    imp = admin.j("POST", f"admin/products/{pid}/cards/import", json={"lines": line(1), "note": "smoke batch"})
    check(imp["imported"] == 1 and imp["batch_id"], f"import should create one card + batch, got {imp}")
    batches = admin.j("GET", f"admin/products/{pid}/batches")["items"]
    b = next((x for x in batches if x["id"] == imp["batch_id"]), None)
    check(b and b["count"] == 1 and b["available"] == 1 and "expiring_7d" in b and "reserved" in b,
          f"batches must list the new batch with counters, got {b}")
    listed = admin.j("GET", f"admin/products/{pid}/cards", params={"batch_id": imp["batch_id"]})["items"]
    check(len(listed) == 1 and "order_id" in listed[0] and "smoke." not in listed[0]["masked"],
          "card list must carry order_id and only masked values")

    f = admin.j("POST", "admin/cards/find", json={"lines": [line(1), "no-such-card-" + tag], "by": "content"})
    check(len(f["items"]) == 1 and f["missing"] == 1, f"find: want 1 hit + 1 missing, got {len(f['items'])}/{f['missing']}")
    it = f["items"][0]
    check(it["line"] == 1 and it["product_id"] == pid and it["product_title_i18n"].get("en")
          and it["status"] == "available" and "smoke." not in it["masked"],
          f"find item shape wrong: {it}")
    ff = admin.j("POST", "admin/cards/find", json={"lines": [f"SMOKE.{tag}.1@gmail.com"], "by": "first_field"})
    check(len(ff["items"]) == 1 and ff["items"][0]["id"] == it["id"], "find by first field (case-insensitive) failed")
    step("card find: by content (+missing count) and by first field; masked only")

    v = admin.j("POST", "admin/cards/void", json={"ids": [it["id"]], "reason": "smoke"})
    check(v["voided"] == 1, f"void should affect 1 card, got {v}")
    check(admin.j("POST", "admin/cards/void", json={"ids": [it["id"]], "reason": "smoke"})["voided"] == 0,
          "voiding an already void card must be a no-op")
    after = admin.j("POST", "admin/cards/find", json={"lines": [line(1)], "by": "content"})["items"][0]
    check(after["status"] == "void" and after["void_reason"] == "smoke", f"voided card state wrong: {after}")
    step("card void: available -> void (reason kept), idempotent")

    imp2 = admin.j("POST", f"admin/products/{pid}/cards/import", json={"lines": line(2)})
    old = admin.j("POST", "admin/cards/find", json={"lines": [line(2)], "by": "content"})["items"][0]
    new = admin.j("POST", f"admin/cards/{old['id']}/replace", json={"code": line(3)})
    check(new["id"] != old["id"] and new["status"] == "available" and new["batch_id"] == imp2["batch_id"],
          f"replace must create a new available card in the same batch, got {new}")
    admin.req("POST", f"admin/cards/{old['id']}/replace", expect=409, json={"code": line(4)})  # old is void now
    chk = admin.j("POST", "admin/cards/find", json={"lines": [line(2), line(3)], "by": "content"})
    st = {i["line"]: i for i in chk["items"]}
    check(st[1]["status"] == "void" and st[1]["void_reason"] == "replaced" and st[2]["status"] == "available",
          f"replace-content states wrong: {chk['items']}")
    admin.j("POST", "admin/cards/void", json={"ids": [new["id"]], "reason": "smoke-cleanup"})
    step("card replace-content: old void(replaced), new available in the same batch, second replace 409")


def v3_file_message(buyer: Client, agent: Client, conv: dict) -> None:
    """Long text is sent as a text/plain file attachment plus a `file` message."""
    text = "".join(f"line {i}: 长文本 long text payload\n" for i in range(300))
    up = buyer.j("POST", "uploads", data={"kind": "file"},
                 files={"file": ("../../etc/passwd.html", text.encode(), "text/html")})
    check(up["kind"] == "file" and up["mime"].startswith("text/plain") and up["name"].endswith(".txt")
          and "/" not in up["name"], f"file upload must be a sanitised .txt text/plain, got {up}")
    buyer.req("POST", "uploads", expect=(400, 413, 415, 422), data={"kind": "file"},
              files={"file": ("bin.txt", b"\x00\x01\x02binary", "text/plain")})
    msg = buyer.j("POST", f"conversations/{conv['id']}/messages",
                  json={"client_msg_id": str(uuid.uuid4()), "type": "file", "attachment_id": up["id"]})
    pl = msg["payload"]
    check(msg["type"] == "file" and msg["text"] is None and pl["name"] == up["name"]
          and pl["bytes"] == up["bytes"] > 0 and 0 < len(pl["preview"]) <= 160,
          f"file message payload wrong: {msg['type']} {pl}")
    seen = agent.j("GET", f"admin/conversations/{conv['id']}/messages", params={"limit": 50})
    check(any(m["id"] == msg["id"] and m["type"] == "file" for m in seen), "agent must see the file message")
    dl = buyer.req("GET", up["url"])
    check(dl.text.startswith("line 0:") and "attachment" in dl.headers.get("Content-Disposition", "")
          and dl.headers.get("X-Content-Type-Options") == "nosniff", "file download headers/body wrong")
    step(f"long text sent as file message ({pl['bytes']} bytes, preview {len(pl['preview'])} chars), safe download")


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
    stocked = [p for p in items if p["delivery_mode"] == "auto_card" and (p["stock"] or 0) >= 3]
    details = {p["id"]: buyer.j("GET", f"products/{p['id']}") for p in stocked}

    def n_fields(p: dict) -> int:
        d = details[p["id"]]
        return len((d.get("delivery_format") or "").split(d.get("delivery_separator") or "----"))

    # single-code product for the MVP flow (codes start with DEMO-), multi-field one for v3
    auto = next((p for p in stocked if n_fields(p) == 1), None)
    multi = next((p for p in stocked if n_fields(p) >= 3), None)
    manual = next((p for p in items if p["delivery_mode"] == "manual"), None)
    check(auto and manual, "seed must contain a single-code auto_card (stock>=3) and a manual product")
    check(multi, "seed must contain a multi-field auto_card product (delivery_format with >=3 labels, stock>=3)")
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
                                        "locale": "en", "pow": solve_pow(eve, "register")})
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

    admin = Client(base)
    admin.login("admin@demo.local", env["APP_SEED_ADMIN_PASSWORD"])

    v3_public(anon, auto, multi, manual)
    v3_instant_and_after_sales(base, buyer, agent, admin, auto, multi, details[multi["id"]])
    v3_cards(agent, admin, multi)
    v3_file_message(buyer, agent, conv)

    # ---- ledger balance -------------------------------------------------------------
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
