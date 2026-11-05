# 24goods — Platform evaluation & recommendation

> Date: 2026-11-05 · Method: four parallel research agents (platforms, chat/voice, payments, infra) read source code, official docs and the live GitHub API, plus independent verification. Evidence and per-claim verification markers are in [`research/`](research/). Stars and push dates are from `gh api` on the date above.

## 1. Executive summary

1. **Don't adopt the previously suggested hybrid (Dujiao-Next + Chatwoot + FastAPI) as-is.**
   - Dujiao-Next **stores card keys in plaintext** (verified in source: `internal/modules/cardsecret/domain/secret.go`). It also has an open refund/delivery race issue ([#380](https://github.com/dujiao-next/dujiao-next/issues/380)) and a fresh public audit of [5 critical / 16 high findings (#374)](https://github.com/dujiao-next/dujiao-next/issues/374), only partly fixed.
   - Chatwoot's customer widget **cannot record voice messages**. Its calls are paid, PSTN-only (Twilio/WhatsApp) and use Google STUN, which is blocked in China. It can't render a real quote card. It needs at least 4 GB RAM, which amounts to a second monolith.
   - The hybrid would also give you two sources of truth for orders and prices, and the negotiation feature lives exactly on that seam.
2. **No open-source system covers the core differentiator**: Xianyu-style chat → custom quote → in-chat payment, plus true multi-tenant resellers. Every card-issuing system checked (Dujiao-Next, dujiaoka, acg-faka) stores card keys in plaintext.
3. **Recommended: Solution A, "own the core, rent the commodities".**
   - A Python/FastAPI modular monolith owns the security-critical domain: card vault, orders, quotes, ledger, tenants.
   - Mature components handle the commodity parts: Centrifugo (realtime), LiveKit or coturn (calls), BEpusdt (USDT, run as a sidecar), Caddy (TLS and custom domains), Nuxt 4 + Vant 4 (mobile EN/ZH UI), PostgreSQL, Redis and R2.
   - Dujiao-Next, acg-faka and Sub2API are used as **design references only** (clean-room, no copied GPL code).
4. **Cloudflare is slow from mainland China on Free/Pro**: often routed via US West, 200–300 ms or worse at peak, and Turnstile is unreliable there. Run **two entry paths**:
   - Cloudflare-proxied main domain for international users, through a tunnel to the core (origin hidden).
   - A grey-clouded CN entry on the second domain, pointing at the SoftBank edge. That host holds no data and can be rotated.
5. **Legal risk you must clear before launch.** PRC regulators treat USDT business as illegal financial activity (银发〔2021〕237号, restated by Yinfa [2026] No. 42), and 四方支付 aggregator operators have been convicted. Host nothing in mainland China; Tencent GZ is a monitoring probe only. Get legal advice before marketing USDT or 四方 CNY payments to mainland users.

## 2. Independent check of the previous recommendation

| Claim / component | Finding | Verdict |
|---|---|---|
| Dujiao-Next as commerce core | Active and well structured (Go, 35 modules, 381 test files, en-US UI, EPay v1/v2 + BEpusdt, callback checks are good). **But:** plaintext card keys and payment-channel secrets; reseller support is "one merchant + sub-stores", with custom domains approved manually and no TLS automation; GPL-3.0 with no CLA; Go, not your stack. | Reference, or interim tool (Solution B) |
| "Card encryption needs careful verification" | Verified: **there is none** for card keys. AES-GCM covers only TOTP and API secrets. | Confirmed concern |
| Chatwoot for chat | MIT core, but calls and branding are paid enterprise features ($19–99/agent/mo); no widget voice recording ([#9183](https://github.com/chatwoot/chatwoot/issues/9183)); cards are link-outs only; ≥4 GB RAM. | Optional **internal** support desk later; not for buyer↔seller |
| Crisp | SaaS; Crisp's own docs say **China may block it**. | Reject |
| FastAPI + PG + Redis + R2 | Right fit for your skills and for a security-first custom domain model. | Keep |
| Sub2API | It's an **AI-API relay/carpool gateway**, not a store. Useful code reference (OAuth, Turnstile, EasyPay tests, idempotency). Note: it moved payment configs *back* to plaintext. | Reference only |

## 3. Commerce / card-issuing platforms

| | [Dujiao-Next](https://github.com/dujiao-next/dujiao-next) | [dujiaoka](https://github.com/assimon/dujiaoka) | [acg-faka](https://github.com/lizhipay/acg-faka) | [Saleor](https://github.com/saleor/saleor) | [Medusa](https://github.com/medusajs/medusa) | [Bagisto](https://github.com/bagisto/bagisto) |
|---|---|---|---|---|---|---|
| Stars / last push | 1.7k / 2026-11-03 | 12.1k / **archived** | 5.9k / 2026-10-28 | 23.5k / 2026-11-04 | 37.1k / 2026-11-04 | 28.3k / 2026-11-05 |
| License | GPL-3.0 | (source removed) | MIT + encrypted proprietary file | BSD-3 | MIT + enterprise | MIT |
| Stack | Go + Vue 3 | Laravel 6 (EOL) | PHP 8 | Python/Django GraphQL | TypeScript | Laravel |
| Card-key stock + auto delivery | ✅ | ✅ | ✅ | ❌ (build it) | ❌ | ❌ |
| Card keys encrypted at rest | ❌ plaintext | ❌ | ❌ | n/a | n/a | n/a |
| Manual delivery | ✅ | ✅ | ✅ | ~ | ~ | ~ |
| Chat negotiation / quotes | ❌ | ❌ | ❌ (tickets) | ❌ | ❌ | ❌ |
| Resellers / white-label | Sub-stores, manual domains | ❌ | 分站 sub-stations | Channels (not resellers) | Sales channels | Multi-vendor add-on |
| EN + ZH | ✅ | partial | ✅ | ✅ | ✅ | ✅ |
| Security posture | Medium (audit #374, #380 open) | Poor | Poor history (XSS→card theft, forged callbacks) | Good, mature | Good | Medium |
| Fit for this project | Reference / interim | ❌ | Study the 分站 model only | Heavy, generic | Not your stack | Not your stack |

Closed SaaS benchmarks: **链动小铺** (consignment with direct clearing, 4% fee, affiliate commission) and **16688** (supplier marketplace, one-click listing, stock sync). Copy their *product ideas* (supply marketplace, one-click listing, D0 settlement), not their code. Details: [research/platforms.md](research/platforms.md).

## 4. Chat, voice and negotiation

Recommendation: **a custom chat domain in FastAPI**, plus these components:
- [Centrifugo](https://centrifugal.dev) v6 (Apache-2.0) for WebSocket/SSE fan-out, presence and history recovery. PostgreSQL stays the source of truth.
- [LiveKit](https://docs.livekit.io/home/self-hosting/) (Apache-2.0) for voice calls, with TURN over TLS on port 443 because cross-border UDP from China is throttled.
- `MediaRecorder` voice messages, uploaded with presigned PUTs.

Why not off-the-shelf:

| Option | Reason it doesn't fit |
|---|---|
| OpenIM | SDK core is AGPL; heavy Mongo + Kafka stack |
| Tinode | GPL |
| Rocket.Chat / Zulip / Matrix | Team-chat models |
| Papercups | Dead |

Full comparison and data model: [research/chat.md](research/chat.md).

## 5. Payments

| Channel | Use | Notes |
|---|---|---|
| [BEpusdt](https://github.com/v03413/bepusdt) v1.24 (GPL-3.0, separate process) | **Primary crypto rail** | Non-custodial, watch-only addresses, many chains. Pitfalls: `amount=0` = "any amount" mode; confirmations default to 0; the `/pay/info` re-query endpoint is unauthenticated, so keep it on the private network |
| EPay / 易支付 protocol adapter | CNY via a ZPay-like aggregator | MD5 V1 has **no timestamp**, so it can be replayed; V2 adds RSA + timestamp. Pin `sign_type`, re-query, check `pid` and amount |
| [ZPay](https://z-pay.cn/doc.html) | Optional CNY rail behind a kill switch | Custodial, about 1.6% fees, D+1 settlement, freeze risk; merchant key goes in query URLs |
| UPay | **Skip** | Abandoned PHP 7.4 code, single commit |
| Cryptomus / NOWPayments | International crypto backup | KYB required, custodial |
| Stripe / PayPal | Probably not viable | Stripe restricts gift cards, virtual credits and in-game items |

Hardened notify pipeline: signature check → merchant → amount → status → server-to-server re-query → row lock and state machine → unique trade number → outbox → double-entry ledger. See [research/payments.md](research/payments.md). The MVP seed already implements and tests the EPay verifier: [`backend/app/payments/epay.py`](../backend/app/payments/epay.py).

## 6. Cloudflare and mainland China

- **Free/Pro plans:** CN traffic often goes to US West edges, with heavy evening packet loss on China Unicom. The [China Network](https://developers.cloudflare.com/china-network/faq/) needs Enterprise + ICP filing, so it's not an option for you.
- **[Turnstile](https://developers.cloudflare.com/china-network/reference/available-products/):** unreliable in CN. Use [ALTCHA](https://github.com/altcha-org/altcha) (MIT, self-hosted proof-of-work) as a fallback and never make Turnstile mandatory. Never use reCAPTCHA.
- **R2:** fine for storage and backups. Presigned URLs work only on `*.r2.cloudflarestorage.com`, and `r2.dev` is rate-limited. For CN users, put a cache on the SoftBank edge in front of R2. **Never store card keys or delivery files in a public bucket.**
- **Reseller custom domains:**
  - International users: [Cloudflare for SaaS](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/plans/), 100 hostnames free, then $0.10 each.
  - CN users: [Caddy on-demand TLS](https://caddyserver.com/docs/automatic-https#on-demand-tls) on the SoftBank edge, with a strict `ask` endpoint that checks a DNS TXT record.
- **Pattern:** two independent entry paths (see architecture §3). If one is attacked or blocked, the other keeps working.
- **Optimized IP (优选IP) tricks:** experiment only. Never rely on third-party CNAMEs.

## 7. Three implementation options

### Solution A — Own the core, rent the commodities ⭐ Recommended

**Stack:**
- FastAPI modular monolith (Python 3.12, SQLAlchemy 2, Alembic, Pydantic 2)
- PostgreSQL 17 with row-level security (RLS) and column grants; Redis 7
- [Centrifugo](https://github.com/centrifugal/centrifugo); LiveKit/coturn (milestone M5)
- BEpusdt sidecar and an EPay adapter
- [Caddy](https://caddyserver.com) with [CrowdSec bouncer](https://github.com/hslatman/caddy-crowdsec-bouncer)
- [Nuxt 4](https://nuxt.com) + [@nuxtjs/i18n](https://i18n.nuxtjs.org) + [Vant 4](https://vant-ui.github.io/vant/) for the frontend
- [SQLAdmin](https://github.com/smithyhq/sqladmin) (BSD-3) for internal ops screens in the MVP
- R2 for images and encrypted backups

**Pros:**
- One language and one source of truth.
- Security invariants are designed in: asymmetric card vault, column-level DB grants, RLS tenant isolation.
- Negotiation is a first-class domain object.
- True multi-tenancy, which prepares for C2C.
- No copyleft entanglement, no per-seat fees.
- Fits your FastAPI skills.

**Cons:**
- The most code to write. Catalog, cart, coupons and an admin UI are rebuilt, which is the work Dujiao-Next gives you for free.
- Security depends on *your* discipline. Mitigation: the CI gates and review agents in this repo, plus external review before launch.

**Effort (estimate, solo developer with AI agents):**
- MVP: about 6–10 weeks.
- Resellers: +4–6 weeks.
- C2C: +6–10 weeks.

**Running cost:** your existing VPSs, the free Cloudflare plan and the free R2 tier (but see the R2 note in architecture §11).

### Solution B — Dujiao-Next core + FastAPI sidecar (fastest to revenue)

Run Dujiao-Next v1.5.x for catalog, card stock, payments and reseller storefronts. Build only the chat and quote service in FastAPI, creating orders through Dujiao's API. Put it behind Caddy and CrowdSec, take admin off the public internet, and keep only low-value card stock in it.

**Pros:**
- Selling within days.
- Mature payment adapters, an English UI, and reseller sub-stores already built.

**Cons:**
- Card keys stay plaintext unless you fork and maintain a Go patch. That means owning a GPL fork; distributing it to agents would force GPL on your code.
- Two sources of truth: quote price ↔ Dujiao order. In-chat price changes need an API that may not exist.
- Inherits open security issues (#380).
- Manual domain approval.
- Not real C2C.
- Go codebase.
- Migrating off it later costs as much as Solution A did.

**Use it only as a stop-gap** for automatic card sales while Solution A is built, with stock you can afford to lose.

### Solution C — Saleor headless + FastAPI apps

Saleor (Python/Django, GraphQL, BSD-3) provides products, channels, checkout, orders and the dashboard. Build the card vault, chat/quotes, payment adapters and resellers as Saleor "Apps" connected by webhooks.

**Pros:**
- Mature and audited commerce core.
- Permissive license.
- Python.
- Good dashboard.

**Cons:**
- Heavy: Django, GraphQL, Celery and a separate dashboard app.
- Every custom flow, including quote → order, card delivery and commission, goes over webhooks, which adds latency and failure modes.
- No marketplace or reseller model.
- Card-key delivery doesn't fit Saleor's model.
- You still write about 60% of the business logic, plus learning Saleor.

**Not recommended** unless you later need complex physical-goods commerce.

### Ranking

| Rank | Option | Why |
|---|---|---|
| 1 | **A** | Long-term, security-first, C2C-ready |
| 2 | B | Interim only, if revenue in the next month matters more than security |
| 3 | C | Rejected |

## 8. Development priority (Solution A)

| Milestone | Scope | Exit criteria |
|---|---|---|
| **M0 ✅** | Repo, CI security gates, card vault, EPay verifier, AGENTS.md, docs | CI green |
| **M1 MVP** | Auth (email + Argon2id, email verification, TOTP for staff, optional Google OAuth); product listing (images + text) and PG search; card stock upload (sealed); orders; BEpusdt + one EPay channel; auto delivery + manual delivery; buyer order page; minimal admin; EN/ZH mobile UI; deploy across SoftBank edge, IIJ core and US backups | A stranger can buy a card key and a manual service end-to-end; restore drill passes |
| **M2 Chat** | Centrifugo; "Chat with seller" auto-attaches the product card; text and images; **quote card → order → pay in chat**; in-chat price change for pending orders; seller online/away/on-hold status; email and Telegram notifications; contact and QR-code bypass detection | A negotiated order is paid from inside the chat |
| **M3 After-sales + hardening** | Refunds, disputes, wallet and double-entry ledger; voice messages; delivery worker moved to an isolated host with OpenBao; reveal budgets; ZAP DAST; external pentest | Pentest has no high findings |
| **M4 Resellers** | Tenants; one-click store clone; product sync with reseller pricing above a floor; commission ledger and settlement; custom domains (Caddy `ask` + Cloudflare for SaaS) | A reseller runs a white-label store on their own domain |
| **M5 C2C + calls** | Merchant onboarding and KYC, escrow, payouts, ratings, LiveKit voice calls, moderation queue | Multiple independent sellers |

## 9. Key risks

| Risk | Impact | Mitigation |
|---|---|---|
| USDT / 四方 payments are illegal in mainland China | Legal exposure, domain blocking | Legal advice; no mainland hosting; rotatable CN entry domain |
| Custodial aggregator freezes funds | Cash loss | Low limits, daily sweep, reconciliation job, kill switch |
| SoftBank IP null-routed during a DDoS | CN users lose service | The Cloudflare path keeps working; rotate the CN entry IP; short TTLs |
| Tokens leak (BEpusdt token, EPay key) | Forged callbacks | Re-query before fulfilment, private network, secret rotation |
| Solo developer misses a security bug | Card or money loss | CI gates, review agents, pentest at M3, bug-bounty email |
| R2 10 GB fills up | Backup failures | Retention policy; a second backup repo on US |
