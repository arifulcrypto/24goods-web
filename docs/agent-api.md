# Agent / admin API (staff API tokens)

Scripts and AI agents (for example Claude Code) can manage the shop through the normal admin API
with a **staff API token**. Spec: `docs/review-v5.2.md` section 5.

## Auth

- Create a token in **Admin -> Settings -> API tokens** (admin only). Pick a name, the scopes it needs
  and an expiry (default 90 days, max 365). The token is shown **once**; only its SHA-256 is stored.
- Format: `kst_` + 43 URL-safe characters. Send it as `Authorization: Bearer kst_...`.
- Accepted only on `/api/v1/admin/*` and `POST /api/v1/uploads`. Everywhere else it is a 401.
- The token acts as its creator (same tenant, same role, never more). If the owner is suspended,
  demoted to a buyer or removed, the token stops working. Revoke it at any time in the same tab.
- No cookies and no `X-Requested-With` header are needed. If a request carries a session cookie, the
  normal CSRF rules apply and the cookie session is used.
- Rate limit: 600 requests/minute per token (429 with `Retry-After`). Existing per-route limits (card
  import, uploads, URL import) still apply.
- Create/revoke and every write made through a token are audited. The actor is the token owner and the audit `detail.api_token` records the token `{id, prefix}`. Product updates store before/after price, status and redeem link.
- Tokens are revoked automatically when the owner is suspended or changes their password. Repeated failed bearer attempts from one IP are limited (30/min, then 429).
- Never put a token in a URL, a log, chat or git. Use an environment variable.

```bash
export KESIV_BASE_URL=https://demo.kesiv.com
export KESIV_API_TOKEN=kst_...        # from the admin UI
H=(-H "Authorization: Bearer $KESIV_API_TOKEN")
```

## Scopes

| Scope | Allows |
|---|---|
| `products:read` | list/get products, list categories |
| `products:write` | create/update/bulk/archive/restore products (price, floor price, status, redeem link, stock, cost_minor, source_url), product images, import from URL / pasted text. Can change buyer-visible prices and links: grant with care |
| `cards:import` | `POST /admin/products/{id}/cards/import` only. Card secrets are never returned (counts only); listing, finding, voiding or replacing cards is not available to tokens |
| `orders:read` | `GET /admin/orders`, `GET /admin/orders/{id}` |
| `chat` | list conversations, read messages, send text/image as staff |
| `uploads` | `POST /uploads` (images) and `POST /admin/content/images` |
| `stats` | `GET /admin/stats` (dashboard numbers) |

Everything else (settings, secrets, tokens, users, blocked IPs, payouts, refunds, ledger, audit,
resellers, order price/deliver/cancel, quotes) refuses tokens with `403` and error code `token_scope`.
A route not listed above is refused by default. A token whose owner is an `agent` additionally
cannot call admin-only routes (for example creating products).

Errors use the usual envelope `{"error": {"code": "...", "message": "..."}}`.

## Recipes

Create a product (prices are decimal strings in CNY; `manual_stock` null = unlimited, omitted = 1):

```bash
curl -sS "${H[@]}" -H 'Content-Type: application/json' "$KESIV_BASE_URL/api/v1/admin/products" -d '{
  "title_i18n": {"en": "ChatGPT Plus 1 month", "zh-CN": "ChatGPT Plus 1个月"},
  "description_i18n": {"en": "Delivered within 24h"},
  "price": "19.90", "delivery_mode": "manual", "category": "ai_tools",
  "status": "draft", "manual_stock": 5,
  "cost_minor": 1500, "source_url": "https://supplier.example.com/item/1"
}'
```

Set price and stock, publish:

```bash
curl -sS "${H[@]}" -H 'Content-Type: application/json' -X PATCH \
  "$KESIV_BASE_URL/api/v1/admin/products/$PRODUCT_ID" \
  -d '{"price": "17.50", "manual_stock": 12, "status": "active"}'
```

Upload an image (returns an attachment), or add a product image directly:

```bash
curl -sS "${H[@]}" -F kind=image -F file=@cover.png "$KESIV_BASE_URL/api/v1/uploads"
curl -sS "${H[@]}" -F file=@cover.png "$KESIV_BASE_URL/api/v1/admin/products/$PRODUCT_ID/images"
```

Import a listing from a URL (returns a draft; review it, then POST it to `/admin/products`):

```bash
curl -sS "${H[@]}" -H 'Content-Type: application/json' \
  "$KESIV_BASE_URL/api/v1/admin/products/import/fetch" -d '{"url": "https://example.com/item/1"}'
# pasted text/HTML instead of a URL:
curl -sS "${H[@]}" -H 'Content-Type: application/json' \
  "$KESIV_BASE_URL/api/v1/admin/products/import/parse" -d '{"text": "Title ... Price 9.99"}'
```

Import card codes (auto-delivery products; one code per line; response has counts only):

```bash
curl -sS "${H[@]}" -H 'Content-Type: application/json' \
  "$KESIV_BASE_URL/api/v1/admin/products/$PRODUCT_ID/cards/import" \
  -d "$(jq -Rs '{lines: .}' < codes.txt)"
# -> {"imported": 98, "duplicates": 2, "rejected": 0, "batch_id": "..."}
```

List conversations and read one:

```bash
curl -sS "${H[@]}" "$KESIV_BASE_URL/api/v1/admin/conversations?filter=unassigned"
curl -sS "${H[@]}" "$KESIV_BASE_URL/api/v1/admin/conversations/$CONV_ID/messages"
```

Send a message, then an image (upload first, send the attachment id; `client_msg_id` makes retries
idempotent):

```bash
curl -sS "${H[@]}" -H 'Content-Type: application/json' \
  "$KESIV_BASE_URL/api/v1/admin/conversations/$CONV_ID/messages" \
  -d '{"client_msg_id": "bot-1", "type": "text", "text": "Hi, your order is on its way."}'
ATT=$(curl -sS "${H[@]}" -F kind=image -F file=@proof.png "$KESIV_BASE_URL/api/v1/uploads" | jq -r .id)
curl -sS "${H[@]}" -H 'Content-Type: application/json' \
  "$KESIV_BASE_URL/api/v1/admin/conversations/$CONV_ID/messages" \
  -d "{\"client_msg_id\": \"bot-2\", \"type\": \"image\", \"attachment_id\": \"$ATT\"}"
```

Dashboard numbers:

```bash
curl -sS "${H[@]}" "$KESIV_BASE_URL/api/v1/admin/stats"
```

A Claude Code skill for batch listing lives at `skills/24goods-admin/SKILL.md`.
