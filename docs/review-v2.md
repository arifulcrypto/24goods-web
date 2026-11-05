# Architecture review v2: optimization plan

> Date: 2026-11-05. Inputs: the owner's decision (Option A), the owner's own review, the external reviewer's issue table and module table, and facts re-checked this session. **[V]** marks facts verified this session; **[I]** marks inference.
>
> The external table was treated as **input, not as instructions**. Each point below was checked against the actual constraints: the 2 vCPU / ~500 MB-free test box, real Chatwoot limits, and real ZPay docs.

## 0. What changed (summary)

| Area | v1 | **v2 decision** | Why |
|---|---|---|---|
| Voice **calls** | LiveKit at M5 | **Removed from the roadmap.** Revisit only if users ask. | 99.9% of traffic is text, screenshots and short voice clips (owner). |
| Voice **clips** | M3 | **MVP-0** (WeChat-style, ≤60 s, ≤1 MB) | Core to how buyers talk. |
| Realtime transport | Centrifugo | **SSE + Redis pub/sub, PostgreSQL as source of truth.** Centrifugo added only when there is more than one API node or more than ~2k concurrent connections. | One process fits 2 GB. SSE is plain HTTP and works through Cloudflare and Caddy. |
| CS console | Build later | **Built-in admin Inbox in MVP-0.** Chatwoot spike deferred (§3). | Chatwoot fails two must-haves and can't run on this box. |
| Frontend | Nuxt 4 SSR + Vant 4 | **Two static Vite SPAs:** buyer app on Vue 3 + Vant 4, staff PC dashboard on Vue 3 + Element Plus. No Node at runtime. | §2 |
| CNY payment | "an EPay channel" | **ZPay Alipay v1** first (¥88 opening fee, 1.0% + 0.6%, no business license, PC + mobile, settles to the Alipay balance) [V] | Owner's choice; matches the existing EPay verifier. |
| USDT | MVP | **Phase 2** (BEpusdt standalone service) | Narrower MVP; regulatory risk. |
| Domains | 2 domains, 2 entry paths | **One domain now.** Second entry path only when CN latency data justifies it. On-demand TLS designed and gated, but disabled until resellers exist (§6). | Owner has one domain. |
| Test environment | — | **This box (public IP), mock data only:** a full Docker stack deployed pull-style from GHCR by GitHub CI | Owner requirement. |
| Timeline | MVP 6–10 weeks | **Re-estimated (§8)** | The external reviewer was right: it was optimistic. |

## 1. Response to the external review

| # | Reviewer finding | Verdict | What we do |
|---|---|---|---|
| 1 | "Centrifugo is not a customer-service system" (High) | **Correct.** Centrifugo is only a transport. | Centrifugo is dropped from the MVP. The CS features are built as product features: conversation list, assignment, unread counts, agent status (online/away/on hold/offline), history in PostgreSQL, quote composer, in-chat price change. This is the admin **Inbox** in MVP-0. See §3 for Chatwoot. |
| 2 | "Card codes can be added via the public API" (High) | **The v1 wording was ambiguous; the risk is real.** "Seal-only" described *key custody*, not who may write. | Card import is `POST /admin/products/{id}/cards/import`. It requires an **`admin`** role (not `agent`), is limited to 5000 lines per request, is rate-limited and audited, and plaintext is never echoed back. Production adds TOTP step-up, an admin-only hostname or VPN, and two-person approval for bulk imports (M2). |
| 3 | "Encryption/decryption key isolation is unclear" (High) | **Fair.** v1 described it but didn't enforce it at the infrastructure level. | Now enforced on four layers (§4): process, container secret, database grant, and network. The key lifecycle is documented, including what happens if a key is compromised. |
| 4 | "The payment callback only describes signature verification" (High) | **Partly.** v1 §7 already listed the state machine, locks and outbox, but no code existed. | MVP-0 implements and tests the full pipeline: signature → server-to-server re-query → row lock → state machine → unique trade number → double-entry ledger → outbox. Tests include **concurrent duplicate notifies**. §5. |
| 5 | "Reseller multi-tenant permissions are unclear" (High) | **Correct.** | Permission matrix in §7. `tenant_id` + PostgreSQL **FORCE RLS** from day one (even with one tenant), plus cross-tenant tests. |
| 6 | "The 5 ms SoftBank/IIJ threshold lacks justification" (Medium) | **Correct.** An arbitrary threshold shouldn't silently drive architecture. | Replaced with a measured latency budget and a manual, ADR-recorded decision. §6.3. |
| 7 | "6–10 weeks is optimistic" (Medium) | **Correct.** | Re-planned in §8 with an independent audit gate before real money. |
| 8 | "CI scanning is not a security audit" (Positive) | **Agreed.** | An independent review or pentest is a release gate (M1 exit). Added to AGENTS.md. |

## 2. Frontend decision

Requirements: 80% mobile buyers; the owner and agents need a PC dashboard; EN primary + ZH; fast in mainland China; small attack surface; must fit a 2 GB box.

| Option | Runtime | Attack surface | Mobile buyer UX | PC admin UX | SEO / link previews | Effort | Verdict |
|---|---|---|---|---|---|---|---|
| **A. Nuxt 4 SSR + Vant 4** (v1) | Node SSR process (~100–250 MB RSS [I]) | Node server, SSR data fetching (SSRF/XSS on the server), Nitro | Good | Vant on PC looks clunky | Best | Medium | ✗. Memory and surface cost buys SEO we barely need. |
| **B. Nuxt 4 `ssr:false` / `nuxt generate`** | Static | Static | Good | Same Vant problem | SPA-level (dynamic product pages can't be prerendered at build) | Medium (Nuxt + Nitro complexity for a SPA) | ✗. Nuxt's conventions without its main benefit. |
| **C. Two Vite SPAs (Sub2API-style)**: `web` (Vue 3 + Vant 4) and `admin` (Vue 3 + Element Plus) in one pnpm workspace with a `shared` package | **Static files served by Caddy** | **Smallest**: no server-side JS | Best (Vant is built for this) | Best (Element Plus is built for PC admin; en/zh-cn locales built in) | Handled by an API share endpoint (below) | Medium | ✅ **Chosen** |
| D. FastAPI Jinja2 + htmx/Alpine ("server-driven reactive") | Python only | Template injection risk, but no Node | OK for catalog, poor for chat (voice recording, SSE, optimistic sends) | Poor for data-heavy admin | Best | High (two paradigms) | ✗ for the app. Its one strength, OG previews, is reused below. |
| E. One responsive app (Tailwind + headless UI) for buyers and staff | Static | Small | Good | Mediocre (admin tables and forms rebuilt by hand) | SPA | High | ✗ |

**Why C.** [Sub2API](https://github.com/Wei-Shaw/sub2api) does the same: a Vue 3 + Vite + Tailwind SPA (`frontend/vite.config.ts`, `tailwind.config.js`) [V] served as static files by its backend. Versions checked on npm [V]: vue 3.5.44, vant 4.10.2, element-plus 2.14.7, vue-i18n 11.4.18, vue-router 5.4.0, pinia 4.1.0, all MIT.

**What a SPA loses, and how we cover it:**
- **Link previews in WeChat, Telegram and X:**
  - **M1:** `GET /s/{product_id}` on the API returns a tiny server-rendered HTML page with OG/Twitter meta (title, price, cover) and a redirect to `/p/{id}`.
  - Share buttons use this URL.
  - No Node is needed.
- **Google SEO:**
  - Traffic comes from Xianyu, Fiverr, G2G and Telegram referrals, and Baidu is moot without an ICP filing.
  - **M1:** a `sitemap.xml` built from the API.
  - **Later, if organic search matters:** a cron job prerenders product pages to static HTML. Still no Node at runtime.
- **First paint in China:**
  - Initial JS budget is ≤150 KB gzip, enforced by a CI script.
  - Vant components are imported on demand.
  - No third-party CDNs.
  - Caddy serves zstd/gzip with immutable caching for hashed assets.

**PC buyers (the other ~20%):** the buyer app renders in a centered phone-width column on desktop (Xianyu's web style), with a wider grid on the home and search pages.

## 3. Chatwoot: test first?

The owner's must-haves:
- the product card is attached automatically
- text, images and **voice clips**
- agent status, including "on hold"
- price changes in the chat
- the buyer pays directly from the chat

| Must-have | Chatwoot (self-hosted, Community) | Built-in Inbox (MVP-0) |
|---|---|---|
| Product card attached automatically | Possible via the API (`cards` message / custom attributes) [V] | ✅ |
| Text and images | ✅ | ✅ |
| **Customer voice clips** | ❌ The widget can't record; only agents can (`AudioRecorder.vue` is dashboard-only, issue [#9183](https://github.com/chatwoot/chatwoot/issues/9183) is open) [V] | ✅ |
| On-hold / busy status | ✅ `online/busy/offline` [V] | ✅ online/away/on hold/offline |
| Price change in chat → buyer pays directly | ❌ Only a link-out card; no live quote state or order binding [V] | ✅ Quote card → accept → pay. Price changes are versioned and audited. |
| Identity | A second user system (contacts), synced by webhooks | One user system |
| Resources | **≥4 GB RAM** (Rails + Sidekiq) [V] | Runs inside the existing API process |
| Fits on this 2 GB test box (~500 MB free)? | **No** | Yes |

**Decision:** don't spend the MVP on Chatwoot. It fails two must-haves (customer voice, in-chat payment) and cannot be tested on the available hardware.

What Chatwoot would add that we lack:
- canned responses
- CSAT surveys
- reports
- a mobile agent app
- multi-channel inboxes (email, Telegram)

**Re-evaluate at M2** if there are three or more agents. The candidate integration is Chatwoot's *API channel* (we keep our buyer UI and push messages to a Chatwoot inbox for agents). Run that spike on a 4 GB server (IIJ or US), not this box. Canned replies, the cheapest of those gaps, are on the M1 list.

## 4. Key isolation: concrete design

**Four isolation layers, all present in the MVP-0 stack** (`deploy/docker-compose.yml`):

| Layer | API container | Delivery container |
|---|---|---|
| Process | `python -m app.serve`: loads **public keys only** (`CardSealer`). It cannot construct a `CardOpener`. | `python -m app.delivery`: the only process that loads private keys (`CardOpener`) |
| Secret | Docker secret `vault_public_keys` | Docker secret `vault_private_keys` (prod: `systemd-creds`, then OpenBao Transit on US at M3) |
| Database | Role `app_api`: `INSERT` on `card_stock`, **no `SELECT` on the `blob` column** (column grant). SQL injection in the API can't even read ciphertext. | Role `app_delivery`: reads `blob`, updates only `status` and `order_id` |
| Network | `edge` + `backend` | **`backend` only (internal: no internet egress)**. A compromised delivery process can't send keys anywhere. |

**Reveal path:**
1. The buyer calls `POST /orders/{id}/reveal`.
2. The API checks the session, then calls `POST delivery:8001/internal/reveal` with an internal token.
3. The delivery service re-checks ownership and that the order is delivered.
4. It decrypts, enforces 10 reveals per hour per order, writes an audit row, and checks the **global circuit breaker**: more than 30 decrypts per minute → refuse and alert.

**Key lifecycle:**

| Stage | Procedure |
|---|---|
| Generate | `python -m app.devtools.keygen`. Prod: on an offline machine. X25519 key pair per key ID (`kid`). |
| Distribute | Public key goes to API config; private key only to the delivery host's credential store |
| At rest | Demo: secret file with mode 0400. Prod: `systemd-creds encrypt` (host-bound), later OpenBao on US so IIJ never stores it |
| Use | In memory in the delivery process only. Never logged, never in env vars, never in core dumps (`ulimit -c 0`). |
| Backup | Encrypted with age to two offline locations. **Never** in the database backup repository. |
| Rotate | New `kid` becomes active for sealing, then a background `rewrap()` job runs. The old `kid` stays decrypt-only until no blob references it, then it is destroyed. |
| **Compromise** | Rotating keys does **not** protect stock that is already stolen. Revoke the key, void all unsold codes sealed under that `kid`, re-import them from the supplier, and notify affected buyers. Runbook in M1. |
| Destroy | `shred` plus removal from the credential store after rewrap reaches 100% |

## 5. Payment: ZPay Alipay v1

**Facts from the ZPay docs** ([fee page](https://v3.z-pay.cn/fee.html), [API doc](https://v3.z-pay.cn/doc.html)) [V]:
- **Protocol:** gateway `https://zpayz.cn`; `submit.php` / `mapi.php`; it says it is 易支付-compatible.
- **Signature:** `md5(sorted k=v&… without sign, sign_type or empty values, not URL-encoded + KEY)`, lowercase.
- **Notify:** sent as **GET**. Reply `success` within 5 s, otherwise it retries at 0/15/15/30/180/1800/1800/1800/1800/3600 s.
- **Order query:** `api.php?act=order&pid=&key=&out_trade_no=`. The **key goes in the URL**.
- **IP allowlist:** no callback source IPs are published.
- **Product names:** they must name the actual goods, otherwise the account may be banned.

**Pipeline (implemented in MVP-0):**

```
GET /api/v1/pay/notify/zpay
 1 verify_notify: pinned MD5, constant-time, pid, out_trade_no, TRADE_SUCCESS, exact Decimal amount
 2 Redis SET NX replay guard (24 h) — protocol has no timestamp
 3 server-to-server re-query (api.php?act=order) — must say paid with same amount; URL never logged
 4 BEGIN · SELECT order FOR UPDATE · already paid → success · only pending_payment → paid
   (expired within grace → manual_review) · INSERT payments UNIQUE(channel, trade_no), UNIQUE(order_id)
   · ledger txn Σ=0 (gateway_clearing / sales_revenue, fee 1.6 %) · INSERT outbox(order.paid) · COMMIT
 5 reply "success" (also for mismatch→manual_review, to stop retries; audited); bad signature → "fail"
delivery worker: outbox → claim cards FOR UPDATE SKIP LOCKED → delivered
```

**ZPay-specific risks:**
- **Personal-channel risk control (风控):** virtual-goods merchants on personal Alipay channels are frequently frozen [I]. Keep descriptive product names, sweep the Alipay balance daily, and keep a kill switch to disable the channel.
- **No IP allowlist:** we rely on the re-query instead.
- **Missed notifies:** the M1 reconciliation job polls the query API for every `pending_payment` order that has a payment attempt.

## 6. Domains, Caddy and latency

### 6.1 Single domain now

The production edge serves `{$SITE_ADDRESS}` (one domain) with standard automatic HTTPS. The demo runs on `:80` on this box's IP: plain HTTP, **mock data only**. For HTTPS during testing, point a subdomain of your domain at the IP and set `SITE_ADDRESS`.

### 6.2 On-Demand TLS hardening (reseller domains, M4)

The pitfall: once On-Demand TLS is enabled, **anyone can point any hostname at our IP**, or send arbitrary SNI values. Each new name triggers an ACME issuance. That burns Let's Encrypt rate limits (300 new orders per 3 h per account, 5 failed authorizations per hostname per hour [V]) and creates CPU, storage and log load. In other words, it is a cheap DoS. Controls:

1. **`ask` is mandatory.** `on_demand_tls { ask http://api:8081/internal/tls/ask }`, already in `deploy/Caddyfile`, commented out until M4.
2. **The ask endpoint is internal-only.** A separate listener on `:8081` that Caddy never routes publicly, so attackers can't use it to enumerate tenant domains.
3. **Allow only verified domains.** It returns 200 only if the hostname exists in `custom_domains` with `status=active` **and** a verified `_24goods.<host>` TXT token. Pending or unknown domains → 404.
4. **Normalize input.** Lowercase, IDNA/punycode, ≤253 characters, no IP literals, no wildcards, no trailing dots → otherwise 400.
5. **Negative-cache and rate-limit.** Unknown names are cached as "no" for 10 minutes, and the endpoint is limited to 30 requests per minute globally (429), so a flood of random SNI can't hammer the database.
6. **Onboarding flow.** The reseller adds a domain, we show a TXT token, a verify button runs the DNS check (plus a CNAME/A check to our edge), and only then is the domain marked `active`. The domain is re-verified daily and suspended if the TXT record disappears.
7. **Observability.** Alert on ask 404/429 rates and ACME failures.
8. **International resellers.** Cloudflare for SaaS custom hostnames (100 free) keep certificate issuance on Cloudflare's side entirely. Which path to use is decided per reseller scale (M4).

Tests for 2–5 are part of MVP-0.

### 6.3 Latency decision rule (replaces "5 ms")

Measure first:
- SoftBank↔IIJ round-trip time over 24 h, p50 and p99 (`mtr` / `ping -i 10`)
- CN carriers → SoftBank (itdog.cn or the Tencent GZ probe)
- database round trips per critical request (target ≤10, logged via SQLAlchemy events)

Rule: if p99 RTT × DB round trips exceeds **30% of the server-side p95 budget (150 ms)**, consider colocating the API with PostgreSQL. Record the decision as an ADR with the measurements. **Never change the architecture automatically.** Example: 2 ms × 10 = 20 ms passes; 12 ms × 10 = 120 ms fails.

## 7. Multi-tenant permission matrix (enforced from M1; resellers arrive at M4)

**Actors:**
- **P** — platform admin
- **S** — supplier owner/staff (owns products and card stock)
- **R** — reseller owner/staff (owns a storefront, domain, buyers and listings)
- **A** — CS agent within a tenant
- **B** — buyer

| Resource | P | S | R | A (own tenant) | B |
|---|---|---|---|---|---|
| Card code **plaintext** | ❌ | ❌ | ❌ | ❌ | ✅ own delivered order, via reveal only |
| Card stock (masked) and import | audit view | ✅ own | ❌ | ❌ | ❌ |
| Products | all | ✅ own (CRUD) | read the supplier catalog shared with them; write own listing price ≥ floor and visibility | read | read active |
| Orders | all (audited) | orders containing their products; **buyer contact masked** | orders from their storefront | their tenant's orders | own |
| Buyer PII | all (audited) | masked | own storefront's buyers | their tenant's buyers | self |
| Conversations | all (audited) | only if escalated or shared | own storefront | own tenant | own |
| Ledger | all | own accounts | own accounts | ❌ | ❌ |
| Custom domains | all | ❌ | own | ❌ | ❌ |

**Enforcement:**
1. The tenant is resolved from the Host header (storefront → tenant).
2. Sessions are bound to tenant and host; reusing a session on another host is rejected.
3. PostgreSQL **ENABLE + FORCE RLS**, with `SET LOCAL app.tenant_id` in every transaction.
4. Supplier visibility goes through explicit share tables and views, never by turning RLS off.
5. Column grants protect secrets.
6. A **cross-tenant test matrix** per endpoint (tenant X's credentials against tenant Y's IDs → 404) runs in CI.

## 8. Revised plan and timeline (solo developer + agents, honest estimate)

| Phase | Scope | Estimate | Exit gate |
|---|---|---|---|
| **MVP-0 (now)** | Local vertical slice on this box with **mock data**: catalog, buy-now, chat (text/image/voice + product card + quote card), in-chat price change, mock ZPay, notify pipeline, auto and manual delivery, reveal, buyer app + PC admin Inbox, CI → GHCR → pull-deploy | days | The owner clicks through the full flow on `http://<ip>/` |
| **M1: production MVP** | Real ZPay; TOTP for staff; email verification; canned replies; reconciliation job; OG share links + sitemap; R2 private bucket; 3-VPS deploy; pgBackRest + restore drill; monitoring; key-compromise runbook | 6–8 weeks | **Independent security review/pentest passed**; restore drill passed |
| **M2** | Refunds and after-sales, disputes, BEpusdt, two-person bulk import, Chatwoot re-evaluation | 4–6 weeks | |
| **M3** | Isolate the delivery host + OpenBao, ZAP DAST, external re-test | 3–4 weeks | |
| **M4** | Resellers: store clone, product sync, pricing ≥ floor, commission ledger, custom domains (§6.2) | 6–8 weeks | Cross-tenant test matrix green |
| **M5** | Multi-merchant C2C: onboarding/KYC, escrow, payouts, ratings | 8–12 weeks | |

**Realistic total:** 4–6 months to reseller-ready with real money. MVP-0 is the cheap way to validate the UX before spending that.

## 9. Final module table (v2)

| Module | Decision |
|---|---|
| Backend | FastAPI modular monolith; no microservices. Exception: **the delivery service is a separate process** for key isolation. |
| Database | PostgreSQL 17, FORCE RLS, column grants |
| Cache / jobs | Redis for sessions, rate limits and pub/sub. **Durable jobs live in a PostgreSQL outbox, not a Redis queue**, because payment → delivery must be transactional. |
| Realtime chat | SSE + Redis pub/sub, messages persisted in PG; Centrifugo later |
| CS console | Built-in Inbox (MVP-0); Chatwoot re-evaluated at M2 |
| Fulfilment | Dedicated delivery worker that alone holds keys, with no internet egress |
| Payment | Unified adapter; **ZPay Alipay v1** first |
| USDT | BEpusdt standalone, phase 2 |
| Voice calls | Dropped (revisit on demand) |
| Domains | One domain now; gated On-Demand TLS ready for M4; Cloudflare for SaaS vs Caddy chosen by scale |
| Frontend | **Two Vite SPAs** (Vant buyer app, Element Plus admin), static, no Node at runtime |
| Files | Private storage served only through authorized endpoints (demo: local volume; prod: private R2 bucket proxied via the edge) |
| Security | CI scanning + cross-tenant tests + **independent audit before real money** |
