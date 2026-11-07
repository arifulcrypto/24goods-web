---
name: 24goods-admin
description: Manage a 24goods / Kesiv shop through its admin API with a staff API token. Use to batch-create or update products from a CSV or JSON file, upload product images, import card codes, or check stats and orders. Reads KESIV_BASE_URL and KESIV_API_TOKEN from the environment.
---

# 24goods admin API

Reference: `docs/agent-api.md` (auth, scopes, all endpoints). This skill is for batch work.

## Rules

- Credentials come only from the environment: `KESIV_BASE_URL` and `KESIV_API_TOKEN`. If either is
  missing, stop and ask the user to set it (token: Admin -> Settings -> API tokens).
- **Never print, echo, log or write the token** to a file, commit or message. Do not run `env`,
  `set -x` or `curl -v`. Pass it only via `-H "Authorization: Bearer $KESIV_API_TOKEN"` (curl) or
  `os.environ` (Python).
- Needs scopes `products:read` and `products:write` (write can change prices and buyer-visible links).
- The token only works with the scopes it was given. A `403 token_scope` means the scope is missing:
  tell the user which scope to add, do not look for workarounds.
- Create products as `"status": "draft"` first, show the user a summary, publish only after they
  confirm. Prices are decimal strings in CNY (`"19.90"`), never floats.
- Never import card codes without being given the codes file by the user. Never try to read cards back.
- Respect rate limits (600/min): sleep briefly between calls and stop on 429.

## Batch-list products from CSV / JSON

Input columns: `title_en, title_zh, price, category, delivery_mode (manual|auto_card), stock, description_en, image` (JSON: array of the same keys).

```python
import csv, json, os, sys, time, urllib.request

BASE = os.environ["KESIV_BASE_URL"].rstrip("/")
TOKEN = os.environ["KESIV_API_TOKEN"]          # never print this

def call(method, path, body=None):
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
for row in rows:
    body = {
        "title_i18n": {"en": row["title_en"], "zh-CN": row.get("title_zh") or row["title_en"]},
        "description_i18n": {"en": row.get("description_en", "")},
        "price": row["price"], "category": row["category"],
        "delivery_mode": row.get("delivery_mode") or "manual",
        "status": "draft",
    }
    if body["delivery_mode"] == "manual" and row.get("stock"):
        body["manual_stock"] = int(row["stock"])
    p = call("POST", "/admin/products", body)
    print("created", p["id"], row["title_en"])      # ids and titles only
    time.sleep(0.2)
```

Images: `curl -sS -H "Authorization: Bearer $KESIV_API_TOKEN" -F file=@img.png "$KESIV_BASE_URL/api/v1/admin/products/$ID/images"`.
Publish after review: `PATCH /admin/products/{id}` with `{"status": "active"}`.
Price / stock changes: `PATCH /admin/products/{id}` with `{"price": "17.50", "manual_stock": 12}`.
