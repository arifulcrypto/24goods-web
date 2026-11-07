# Review v5.2 — owner feedback batch (2026-11-07)

Rules: AGENTS.md §7 (2 GB box: ≤ 2 agents, targeted local tests, full suite in CI). One implementer
at a time, in this order. Each item → PR, CI green, merge, demo update. No Cloudflare changes
without the owner.

## Diagnosis (verified 2026-11-07 on the demo)

- **"Human verification failed" after admin login.** Demo has Turnstile on with action `login`
  (test keys). The admin `LoginPage.vue` never renders the widget nor sends `turnstile_token`, so
  every staff login is refused with `turnstile_failed`. Bug, not config.
- **`{"code":429,"msg":"Access is too frequent…"}` for staff.** Demo global limit is 20 req / 60 s
  per IP (`tenants.settings.rate_limit`). Only `/api/v1/admin/*` is exempt; the admin SPA also calls
  `/site`, `/auth/*`, `/uploads`, `/chat`, so staff hit it. Global limit has no staff/IP allowlist.
- Manual-delivery products have no stock count (only card stock for auto delivery).

## 1. Staff access: Turnstile + rate limit (P0, bug)
- Admin login renders the Turnstile widget when `login` is protected (reuse shared `TurnstileWidget`,
  reset after every attempt). Web forms: verify reset-after-attempt everywhere.
- New `security.staff_ip_allowlist` (list of IPs/CIDRs, validated, max 50) in Settings → Security.
  Allowlisted client IPs skip Turnstile and the global rate limit (and the login/IP throttles where
  cheap). Client IP = the existing trusted-proxy `client_ip`.
- Global limiter: before answering 429, a request with a valid **staff** session is let through
  (lookup only on the over-limit path, so normal cost is unchanged).
- Demo: raise the limit to the default (120 / 60 s) and add the owner's IP to the allowlist when known.
- Tests: admin login with Turnstile on; allowlisted IP bypass; staff session not 429'd; buyer still
  limited; spoofed `X-Forwarded-For` from an untrusted peer does not bypass.

## 2. Manual-delivery stock (P0)
- `products.manual_stock` (int ≥ 0, nullable = unlimited is NOT the default): default **1** for new
  manual products; admin can edit. Order creation reserves (decrement under row lock), cancel/expiry
  releases, `out_of_stock` when 0. Existing manual products migrate to 1? → no: migrate to NULL
  (= unlimited) so live listings don't vanish; new products default 1. Show stock in admin + web.
- Audit row on stock edits.

## 3. Paste images with Ctrl+V (P1)
- Shared composable `usePasteImage(onFile)` (clipboard image → File → existing upload endpoint;
  same magic-byte validation server side). Wire into: admin manual delivery attachment, admin CS
  inbox chat, web chat, product images (ProductDialog), settings image fields.

## 4. README en + zh-CN (P1)
- `README.md` (English, primary): intro, features, stack, quick start, deployment (demo/prod),
  security model, known issues / roadmap, links. `README.zh-CN.md` (Chinese), language switch link
  at the top of both.

## 5. Agent / admin API (P1, security-sensitive)
- Staff **API tokens**: created in Admin → API tokens (admin only), shown once, stored as SHA-256,
  scopes (`products`, `cards:import`, `chat`, `uploads`, `stats`, `orders:read`), expiry, revoke,
  last-used; audited. `Authorization: Bearer kst_…` accepted on `/api/v1/admin/*` routes that match the
  token scopes, with the token owner's tenant + role (never more than the creator's role).
- Covers: products CRUD + import (URL/paste), card import (never returns secrets; counts only),
  chat: list conversations, send text/image to a buyer, upload image, site stats.
- `docs/agent-api.md` + OpenAPI tag; a Claude Code skill at `skills/24goods-admin/SKILL.md` with
  curl recipes for batch listing.
- security-reviewer pass mandatory.

## 6. SEO for the international store (P1)
- Static SPA stays (ADR-009); add **server-rendered HTML for crawlers and first paint**: the API
  renders `index.html` with per-route `<title>`, meta description, canonical, `hreflang` en/zh-CN,
  Open Graph/Twitter, JSON-LD (`Product`/`Offer`, `BreadcrumbList`, `Organization`) and the product
  content in the body for `/`, product and category routes (Caddy routes those paths to the API).
- Dynamic `/sitemap.xml` (products, categories, static pages; `lastmod`), `/robots.txt` (sitemap
  link; disallow `/admin`, `/api`, checkout/order pages), no `noindex` on public pages (keep on admin,
  white-label hosts per setting), real **404** status + designed 404 page for unknown routes/products.
- Setting `seo.indexing_enabled` (demo: off → `noindex` + robots disallow; prod: on).

## 7. Payments for the demo: USDT + method toggles (P1, payment-auditor)
- = review-v5.1 §4 (BEpusdt, Arbitrum One + X Layer, TRC20 optional) with the owner's addresses.
- Admin Settings → Payments: enable/disable each buyer method (Alipay, USDT) — existing channel
  `enabled` flags; checkout shows only enabled ones.

## 8. Entry check (P2)
- Optional "verify visitors on entry" (Turnstile, invisible/managed) for the web app, optionally admin;
  the pass is a short-lived signed cookie. The Cloudflare 5-second challenge is a Cloudflare setting
  (WAF managed challenge / Under Attack) → owner decision, not changed by agents.

## 9. Production (owner)
- Prod target: Japan VPS; domain decision below. Not executed by agents (Cloudflare + prod deploy).

## Open owner questions
- Prod domain: main site on apex `kesiv.com` with `shop.kesiv.com` → 301, or keep `shop.kesiv.com`
  with apex → 301 to it? Which Japan VPS (the existing IIJ host?).
- "U-Pay": USDT via BEpusdt, or a specific gateway?
- Staff IP(s) for the allowlist.
