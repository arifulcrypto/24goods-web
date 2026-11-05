# Research: Card-issuing & commerce platforms

> Researched 2026-11-05 by a research sub-agent that shallow-cloned and **read** (never executed) the source. **[V]** = verified in source / `gh api`. **[S]** = docs, issues, third-party pages. **[I]** = inference.

## 1. Dujiao-Next — https://github.com/dujiao-next/dujiao-next · https://dujiao-next.com

- Org repos [V]: `dujiao-next` (1,716★, 436 forks, 15 contributors, created 2026-02-10, v1.5.2 on 2026-11-02, GPL-3.0), `document`, `community-projects`. Now a monorepo (backend + storefront + admin).
- **Stack [V]:** Go 1.26 + Gin + GORM, SQLite or PostgreSQL, optional Redis/asynq; Vue 3 + Vite + TS + Tailwind v4 (admin: shadcn-vue); single binary with embedded frontends. ~119k Go LOC, 381 test files, 35-module modular monolith with layering enforced by tests; Casbin RBAC, TOTP 2FA, separate admin/user JWT realms.
- **Features [V]:** SKUs, cart, coupons, promotions, gift cards, wallet, member levels, affiliate, card-secret stock, manual fulfilment, order risk control, inter-site supply (`procurement`, `upstreamapi`, `siteconnection`, `channelapi`), Telegram bot, audit log. Gateways: alipay, wechatpay, **epay v1 MD5 + v2 RSA**, **bepusdt**, epusdt, tokenpay, okpay, paypal, stripe, dujiaopay. Google/Telegram login, Turnstile + image captcha. i18n zh-CN / zh-TW / **en-US**.
- **Reseller [V]:** `internal/modules/reseller` — subdomains + custom domains, white-label config; tenant resolved from Host. Custom-domain verification is **admin manual approval only** (`management.go` ~L491); no DNS TXT check, no ACME automation. One merchant + reseller storefronts on one DB — **not true multi-tenancy** [I].

### ⚠️ Card-key storage [V]

- `internal/modules/cardsecret/domain/secret.go`: `Secret string gorm:"type:text;not null"` → **card keys stored in plaintext**. Delivered copy in `fulfillment.Payload` (text) also plaintext.
- `internal/crypto/aes.go` AES-256-GCM (key = SHA-256 of `app.secret_key`, no KDF, no rotation) is used **only** for TOTP secrets and site/channel API secrets.
- Payment channel `ConfigJSON` (merchant keys, private keys) **plaintext in DB**, only redacted in admin API responses.
- Guest order password = HMAC-SHA256 (fast hash, not bcrypt/argon2).
- Good: refuses to start with weak secrets; stock reservation uses `SELECT … FOR UPDATE` (no-op on SQLite).

### Payment callbacks [V] — solid

EPay MD5/RSA compared with `hmac.Equal`; `VerifyCallbackOwnership` merchant-id check; exact amount + currency + channel + order match (`payment_service_callback.go:148`); idempotent, transactional with row locks. **No callback IP allowlist.** Redis rate limits on callbacks, login, 2FA, guest lookup, gift-card redeem.

### Security track record [S]

- Issue **#374** (2026-10-25) public audit: 5 critical / 16 high / 33 medium / 49 low — default `admin/admin123` + online-update → RCE, admin self-escalation, support role resetting customer 2FA, **refund/fulfilment race delivering keys after refund**, stored XSS via `v-html`, SSRF, **support exporting all card keys without audit**. v1.5.1 fixed default password, super-admin protection, DOMPurify. Maintainer: "partially fixed, some by design".
- Issue **#380** (refund/delivery races, downstream idempotency TOCTOU) **still open**.
- Issue #313: no private vulnerability disclosure channel; no GHSAs/CVEs.

### License

GPL-3.0, no CLA. Hosting a modified copy as SaaS = no source obligation (not AGPL). **Distributing builds to agents/customers** = whole linked binary must be GPL. Proprietary code only safe as a separate process over HTTP/RPC [I].

## 2. assimon/dujiaoka — **archived**

12,132★; repo **force-reset to a README-only commit on 2026-10-22** [V]; development stopped 2026-02-12, points to Dujiao-Next. Laravel 6 (EOL 2022), PHP 7.4, dcat-admin (CVE-2025-65656 CVSS 9.8, CVE-2024-54775 exploited XSS). Card keys plaintext; EPay sign compared with loose `!=`; no `lockForUpdate` on callback → race [I]. **Do not use.**

## 3. lizhipay/acg-faka (异次元发卡) — https://github.com/lizhipay/acg-faka

5,888★, v3.8.5, PHP 8 + MySQL. **License:** MIT *except* `kernel/Plugin.php` — encrypted proprietary licence-check component, reverse engineering forbidden; README adds commercial-use restrictions. Gateways only via paid app store. Features: auto/manual delivery, wholesale/member pricing, **分站 sub-stores** (subdomain/own domain, `app/Model/Business.php`), level-difference commission, store-to-store supply, 80+ hooks, zh/en/ja, 2FA/passkeys, CSP.
Security [V]: card keys plaintext; AES util = AES-128-CBC with **key as IV** + `unserialize()`; legacy `sha1(md5())` path remains; callbacks now check sign/status/amount + optional IP allowlist. History: CVE-2023-43971, issues #239 (XSS → admin cookie → card theft), #245 (arbitrary file overwrite), #767, #781 ("zero-yuan purchases"), #817 (forged BEpusdt callback), #979 (weak JWT key, public log download). **Study its reseller/分站 model; don't use as a base.**

## 4. Sub2API — https://github.com/Wei-Shaw/sub2api

45,466★, LGPL-3.0 + CLA, v0.2.19. **An AI-API relay / subscription-sharing gateway**, not a marketplace (README warns of upstream ToS risk). Go + Gin + Ent + PG + Redis, Vue 3. Useful **as code reference only**: Google/OIDC/WeChat OAuth, Turnstile, SMTP, redeem codes, idempotency records, payment audit log, EasyPay notify-security test suite. Note: payment configs were migrated **from AES-GCM back to plaintext JSON**. CVE-2026-27812 (password-reset Host-header poisoning), CVE-2026-73079, GHSA-vrxq-qm4h-6hgg (path traversal).

## 5. Closed SaaS benchmarks

- **链动小铺** (wzyp.cn / ldxp.cn) [S]: closed hosted 寄售 consignment with 直清 (direct-to-merchant, D0), 分销 commission, auto delivery; **4% fee** ([10100.com](https://www.10100.com/article/58694005)). Unverified breach claim.
- **16688** (16688.com.cn) [V meta]: "专业数字卡密自动交易平台" — free store, 货源广场 supplier marketplace, 一键铺货 one-click listing, 密价采购, stock sync, direct-clearing.
→ UX/feature benchmarks only.

## 6. Other candidates [V via gh api]

| Repo | ★ | License | Last push | Note |
|---|---|---|---|---|
| [Baiyuetribe/kamiFaka](https://github.com/Baiyuetribe/kamiFaka) | 2,327 | MIT | 2023-12 | Python + Vue 3, stale |
| [lizhipay/mcy-shop](https://github.com/lizhipay/mcy-shop) | 502 | MIT | 2026-09 | acg-faka author |
| [zhinianboke/xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply) | 8,124 | AGPL-3.0 | 2026-11 | Xianyu chat auto-reply/delivery — UX reference |
| [GMWalletApp/epusdt](https://github.com/GMWalletApp/epusdt) | 3,950 | GPL-3.0 | 2026-07 | USDT gateway (separate service) |
| [saleor/saleor](https://github.com/saleor/saleor) | 23.5k | BSD-3 | 2026-11 | Python/Django GraphQL headless commerce |
| [medusajs/medusa](https://github.com/medusajs/medusa) | 37k | MIT + enterprise | 2026-11 | TypeScript headless commerce |
| [bagisto/bagisto](https://github.com/bagisto/bagisto) | 28k | MIT | 2026-11 | Laravel, multi-vendor add-on |

None of Saleor/Medusa/Bagisto has card-key stock, chat-negotiation or reseller white-label out of the box.

## 7. Comparison

| | Dujiao-Next | dujiaoka | acg-faka | Sub2API | 链动/16688 |
|---|---|---|---|---|---|
| Maturity | ~9 mo, fast-moving, well structured | Dead | ~5 yrs | Popular, other domain | Production SaaS |
| Card auto + manual | Yes | Yes | Yes | Redeem codes | Yes |
| Reseller / white-label | Sub/custom domain, manual approval, no TLS automation | No | 分站 + supply chain | No | 分销, 货源 |
| Chat / negotiation | No | No | Tickets | No | Unknown |
| English UI | Yes | Partial | Yes | Yes | No |
| License | GPL-3.0 (no CLA) | MIT (source removed) | MIT + encrypted proprietary | LGPL-3.0 + CLA | Closed |
| Card keys at rest | **Plaintext** | Plaintext | Plaintext | n/a | Unknown |
| Callback checks | Strong (no IP allowlist) | Weak | Good now | Good | — |
| Security posture | Medium (big audit, #380 open) | Poor | Poor history | Medium (CVEs) | Unknown |
| Customisation | Medium (Go) | — | Med-high (PHP + encrypted core) | n/a | Impossible |
