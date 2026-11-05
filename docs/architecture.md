# Architecture — 24goods

Status: **v0.1 (M0)** · Owner: repo maintainer · Rationale for the stack: [analysis.md](analysis.md).

Priorities, in order:
1. **Security**
2. **Correctness of money and stock**
3. **Speed from mainland China**
4. **Developer velocity**

## 1. System context

```
           International users                         Mainland-China users
                   │                                             │
         www.<domain-A> (CF proxied)                 cn.<domain-B> (DNS-only, grey cloud)
                   │                                             │
        ┌──────────▼──────────┐                      ┌───────────▼────────────┐
        │ Cloudflare edge     │                      │ SoftBank JP  "EDGE"    │
        │ WAF, rate rule, DDoS│                      │ Caddy (HTTP/3, on-demand│
        │ CF-for-SaaS domains │                      │ TLS), CrowdSec+AppSec, │
        └──────────┬──────────┘                      │ Nuxt SSR, Centrifugo,  │
                   │ cloudflared tunnel              │ R2 image cache,        │
                   │ (no inbound ports)              │ (M5) LiveKit/TURN:443  │
                   │                                 │ NO DB · NO KEYS        │
                   │                                 └───────────┬────────────┘
                   │                   WireGuard mesh (10.8.0.0/24)  │
        ┌──────────▼───────────────────────────────────────────────▼─┐
        │ IIJ JP  "CORE"                                              │
        │ FastAPI API (seal-only vault) · worker (outbox, notifies)   │
        │ delivery-worker (separate unix user, holds vault priv key)  │
        │ PostgreSQL 17 primary · Redis 7 · BEpusdt (private only)    │
        └──────────┬──────────────────────────────────────────────────┘
                   │ streaming replication + pgBackRest/WAL + restic
        ┌──────────▼──────────────────────────────┐      ┌───────────────────────┐
        │ US UT  "DR / OPS"                        │      │ Tencent GZ "PROBE"    │
        │ PG async replica (promotable)            │      │ synthetic checks from │
        │ pgBackRest repo1 (+ repo2 = R2, encrypted)│     │ inside CN: latency,   │
        │ Uptime Kuma / Grafana / Loki             │      │ blocking, Turnstile.  │
        │ (M3) OpenBao Transit / KEK service       │      │ push-only token. NO   │
        │ monthly restore drill                    │      │ user data, NO keys.   │
        └──────────────────────────────────────────┘      └───────────────────────┘
```

## 2. Server allocation

| Host | Role | Holds data? | Exposed ports |
|---|---|---|---|
| **SoftBank JP** (best CN route) | CN entry edge: Caddy, CrowdSec, Nuxt SSR (stateless), Centrifugo, R2 read-through image cache, later LiveKit/TURN | **No** (cache only) | 80/443 TCP+UDP (HTTP/3), later TURN 443/3478 |
| **IIJ JP** | Core: API, workers, delivery worker, PostgreSQL primary, Redis, BEpusdt | **Yes** | **None publicly.** cloudflared egress tunnel; WireGuard UDP from peers only |
| **US UT** | DR and ops: PG replica, backup repo, monitoring, restore drills, later OpenBao | Replica + encrypted backups | WireGuard only (SSH via WG) |
| **Tencent GZ** (no ICP) | CN-vantage probe only | No | None. Outbound only |

Rules:
- SSH listens only on WireGuard interfaces, with keys only, behind CrowdSec.
- nftables default deny on every host.
- **Measure SoftBank↔IIJ RTT first.** If it is above 5 ms, colocate the API on SoftBank and keep only PG on IIJ.
- MVP fallback (fewest moving parts): everything core on IIJ, edge on SoftBank, backups on US. That *is* the plan above, minus OpenBao.

## 3. Traffic entry paths

1. **International path:** `www.<domain-A>` is proxied by Cloudflare, which forwards over a `cloudflared` tunnel to IIJ. The origin IP is never published. One Cloudflare rate rule covers `/api/auth/*`.
2. **CN path:** `cn.<domain-B>` is grey-clouded to SoftBank, then goes over WireGuard to IIJ.
   - The frontend suggests switching paths using a latency probe.
   - TTL is 120 s.
   - **Kill switch:** if this path is attacked, orange-cloud it (slower, but protected) or rotate the IP and hostname.
3. **Reseller domains:**
   - International: Cloudflare for SaaS custom hostnames that CNAME to `customers.<domain-A>`.
   - CN: point an A record at the SoftBank edge. Caddy on-demand TLS asks `GET /internal/tls/ask?domain=`, which returns 200 only if the domain is registered, verified by a TXT record `_24goods.<domain>` = token, and active.
4. **Captcha:** Turnstile when reachable, falling back to ALTCHA proof-of-work. The server accepts either.

## 4. Components

| Component | Choice | Notes |
|---|---|---|
| API | FastAPI 0.16x, Pydantic 2, SQLAlchemy 2 async + asyncpg, Alembic | Modular monolith: `auth`, `catalog`, `inventory` (vault), `orders`, `payments`, `chat`, `quotes`, `ledger`, `tenants`, `admin` |
| Jobs | `arq` or SAQ on Redis + PG **outbox** table | Outbox rows are written in the same transaction as the state change |
| Realtime | Centrifugo v6 OSS | API publishes; clients subscribe to `user:{id}` channels with JWTs issued by the API |
| Calls (M5) | LiveKit OSS (TURN/TLS:443) or P2P + coturn | Tokens only for conversation participants |
| Frontend | Nuxt 4 + @nuxtjs/i18n v10 (`prefix_except_default`, EN default) + Vant 4 | SSR/SWR for listing and product pages; SPA for account, checkout, chat and seller console |
| Admin (MVP) | SQLAdmin (BSD-3) on an internal-only hostname (WG/VPN) | Replaced by the Nuxt seller console at M4 |
| Search | PostgreSQL FTS + `pg_trgm` (CJK via `zhparser` or bigram) | Meilisearch only if needed later |
| Object storage | R2: `media-public` (product images) and `media-private` (chat attachments, presigned) | Card keys never go to R2 |
| Edge | Caddy 2.11 + caddy-crowdsec-bouncer (AppSec) + rate limits | |
| Payments | BEpusdt sidecar, EPay adapter (ZPay), adapter interface for future channels | |
| Secrets | SOPS + age in git for config; systemd `LoadCredentialEncrypted` for vault keys; OpenBao (M3) | |

## 5. Data model (core tables)

```
tenants(id, kind[platform|supplier|reseller|merchant], parent_id, status, settings)
users(id, email, pw_hash argon2id, totp_secret_enc, locale, status)
memberships(user_id, tenant_id, role)                       -- RBAC per tenant
products(id, tenant_id, title_i18n jsonb, desc_i18n, images[], price_minor, currency,
         delivery_mode[auto_card|auto_api|manual], status, search_tsv)
listings(id, tenant_id, product_id, price_minor, status)    -- reseller views of supplier products
card_stock(id, tenant_id, product_id, blob bytea, fingerprint, kid, status[available|reserved|sold|void],
           order_id, created_by, created_at)  UNIQUE(tenant_id, fingerprint)
orders(id, tenant_id, storefront_id, buyer_id, seller_tenant_id, quote_id UNIQUE NULL,
       amount_minor, currency, version, state, channel, expires_at)
order_events(order_id, from_state, to_state, actor, reason, at)   -- audit
payments(id, order_id, channel, gateway_trade_no, amount, raw jsonb) UNIQUE(channel, gateway_trade_no)
deliveries(id, order_id, kind, card_stock_id, sealed_payload bytea, reveal_count, first_revealed_at)
conversations / participants / messages / attachments / quotes   -- see research/chat.md §5
ledger_accounts / ledger_entries(txn_id, account_id, amount_minor)  -- SUM per txn = 0
custom_domains(id, tenant_id, hostname UNIQUE, verify_token, verified_at, tls_mode[caddy|cf_saas], status)
outbox(id, topic, key, payload, created_at, processed_at)
audit_log(id, actor, tenant_id, action, target, before, after, ip, at)
```

Money is `bigint` minor units plus `currency`. Never `float`.

**Tenant isolation:**
- Every tenant table has `tenant_id`.
- PostgreSQL **RLS** policies are keyed on `current_setting('app.tenant_id')`, which is set per transaction by the repository layer.
- Every module's tests include a cross-tenant access attempt.

## 6. Card-key anti-theft design

Threats in scope:
- DB dump, SQL injection or a leaked backup
- A compromised web tier
- A malicious or compromised admin or support account
- Row swapping
- Refund-then-deliver races (cf. Dujiao-Next #380)
- Order lookup by enumeration
- Log leakage

| # | Control | Where |
|---|---|---|
| 1 | **Asymmetric envelope encryption.** Each card gets a random DEK and AES-256-GCM; the DEK is wrapped through X25519 + HKDF + AES-GCM ([`cardvault.py`](../backend/app/core/cardvault.py)). The **API holds only public keys** (`CardSealer`), so it can add stock but cannot read it. | M0 ✅ |
| 2 | **AAD binding** to (tenant, product, card_id), so a cheap card's ciphertext copied onto an expensive product fails to decrypt. | M0 ✅ |
| 3 | **Keyed tenant-scoped fingerprint** (HMAC-SHA256) under `UNIQUE(tenant_id, fingerprint)`, to reject duplicate or recycled stock without decrypting it. | M0 ✅ |
| 4 | **Column-level DB grants.** Role `app_api` can `INSERT` into `card_stock.blob` but has no `SELECT` on `blob` or `deliveries.sealed_payload`. Only role `app_delivery` can read them. Even SQL injection in the API cannot exfiltrate ciphertext. | M1 |
| 5 | **Isolated delivery worker.** It runs as a separate Unix user and systemd unit, with the private key from `LoadCredentialEncrypted`; it is never in env vars and never on disk in plaintext. It consumes outbox `order.paid` events and, in **one transaction**, does three things: (a) locks the order, (b) checks the state is `PAID` and that no refund or dispute exists, (c) claims stock with `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1`, sets it to `sold`, and transitions the order to `DELIVERED`. Then it opens the card and re-seals it under the buyer delivery key. | M1 |
| 6 | **Reveal flow.** Buyers view a code through `POST /orders/{id}/reveal`, which requires an authenticated owner or a one-time signed link sent by email. The delivery worker decrypts on demand. Each reveal increments `reveal_count`, is audited, and is rate-limited per buyer and globally. | M1 |
| 7 | **Circuit breaker.** More than N decrypts per minute, or decrypts for orders not in `DELIVERED` state, halts the delivery worker and sends a Telegram/email alert. This stops bulk dumping even after app compromise. | M1 |
| 8 | **Admins never see plaintext.** Lists show `mask()` only. Exports require two-person approval (M3), are watermarked, audited and time-boxed. | M1 / M3 |
| 9 | **Refunds vs delivery.** A refund takes the same order lock; `DELIVERED → REFUNDED` requires manual review and voids the code where the supplier supports it. Stock reservation expires automatically. | M1 |
| 10 | **Guest lookup.** Order numbers are random 128-bit IDs, not sequential. Lookup requires email + order ID + a code from the emailed link, with rate limits. | M1 |
| 11 | **Key custody and rotation.** Each key ID (`kid`) has a private key backed up offline (age-encrypted, two copies, optionally Shamir-split). Rotation: a new `kid` becomes active and a background `rewrap()` job runs. At M3 the private key moves into a KEK service / OpenBao Transit on US, so IIJ no longer holds it at rest. | M0 / M3 |
| 12 | **No plaintext in logs.** structlog redaction processors; Sentry/Loki scrubbing; tests assert codes never appear in captured logs. | M1 |

## 7. Payment pipeline

```
create order (server price) ─► adapter.create() ─► gateway
gateway notify ─► /pay/notify/{channel}
   1 pinned-alg signature, constant-time   (epay.verify_signature ✅)
   2 merchant id / out_trade_no / status / exact Decimal amount   (epay.verify_notify ✅)
   3 Redis SET NX replay guard (+ timestamp ±300 s where the channel supports it)
   4 server-to-server re-query (BEpusdt /api/v1/pay/info over WG; EPay api.php?act=order)
   5 BEGIN; SELECT order FOR UPDATE; only PENDING→PAID; INSERT payments (UNIQUE channel,trade_no);
     INSERT outbox(order.paid); COMMIT
   6 mismatch / late / overpaid → MANUAL_REVIEW (never auto-fulfil)
return_url → display only
daily reconciliation job: gateway statements vs payments table → alert on drift
```

BEpusdt specifics:
- Always send `amount > 0`.
- Set `block_offset_confirm > 0`.
- Use `classic` match mode.
- Use watch-only addresses.
- Keep the admin port and `/pay/info` reachable only over WireGuard.

## 8. Xianyu-style chat and price negotiation

```
Product page ── "Chat with seller" ──► POST /conversations {product_id}
   → find-or-create (buyer, seller, product)  → system message: product_card snapshot
Seller ── "Send quote" ──► POST /quotes {amount, qty, terms, expires_in}
   → quotes row (status=offered, version, HMAC signature) → message: quote_card
Buyer ── "Accept & pay" ──► POST /quotes/{id}/accept
   → TX: lock quote, check offered & not expired & signature → create order(pending_payment,
         amount = quote.amount) → message: order_card → returns checkout (channel picker)
Seller may change price while pending_payment: PATCH /orders/{id}/price
   → version++, order_price_changes audit row, system message, old payment intents voided
Payment notify → PAID → delivery (auto) or seller "Mark delivered" (manual, with proof upload)
```

**Message types:** `text | image | voice | product_card | quote_card | order_card | system`.

**Seller status:** `online | away | on_hold | offline`. Centrifugo presence plus an explicit status flag; `on_hold` shows the buyer an expected response time.

**Anti-bypass:**
- Detect contact info and off-platform payment attempts: WeChat, QQ, Telegram, Alipay transfer, raw USDT addresses, and full-width or Chinese-numeral obfuscation.
- Detect payment QR codes in images.
- On a hit: warn, mask the content and flag it for review.

**Retention:** chats are encrypted at rest (app-level envelope encryption, a per-tenant monthly DEK). There is no E2EE, because the platform must be able to arbitrate disputes.

## 9. Resellers and multi-tenancy (M4)

- **One-click store cloning:** the clone copies the storefront config (theme, logo, i18n texts, navigation) and creates `listings` that **reference** supplier products. Stock is never copied, and card keys always stay under the supplier tenant.
- **Pricing:** the reseller sets `listing.price ≥ product.floor_price`, which the server enforces.
- **Settlement:** each paid order posts one balanced ledger transaction:

  ```
  buyer_clearing  -price
  supplier_payable +cost
  reseller_payable +(price - cost - platform_fee)
  platform_revenue +platform_fee
  ```

  Payouts are batched weekly above a threshold, with manual approval.
- **Sync:** supplier price or stock changes are pushed to listings through the outbox. A listing is auto-hidden if its price falls below the floor.
- **Domains:** see §3.3. The certificate `ask` endpoint is rate-limited, and unverified domains are never issued certificates.

## 10. Security controls by threat

| Threat | Controls |
|---|---|
| DDoS (L3/L4) | Cloudflare on the international path; provider protection plus a rotatable IP on the CN edge; the core is never exposed |
| CC / L7 floods | Cloudflare rate rule; Caddy rate limits; CrowdSec (scenarios + AppSec); Redis token buckets per IP/user/route; ALTCHA proof-of-work on auth, order and chat-create; cached SSR pages |
| Card-key theft | §6 controls 1–12 |
| Forged payment callbacks | §7 steps 1–6; tests in `test_epay.py` |
| Price tampering | Server-side prices only; signed, versioned quotes; audited price changes |
| Unauthorized access / IDOR | Per-tenant RBAC; RLS; ownership checks in every handler; Argon2id; TOTP mandatory for staff; short-lived JWT + rotating refresh token bound to device; admin only over WG/VPN |
| Vulnerability exploits | CI: Ruff (incl. bandit rules), Bandit, Semgrep, CodeQL (when enabled), pip-audit, Trivy, gitleaks, Dependabot; ZAP baseline on staging (M3); SHA-pinned actions |
| Malicious uploads | Magic-byte check, size limits, re-encoding (Pillow/pyvips), EXIF strip, ClamAV, random object keys, separate media domain, `Content-Disposition`, no SVG |
| Data leaks | App-layer encryption for card keys, chat bodies, TOTP and payment-channel secrets; column grants; log redaction; least-privilege DB roles; encrypted backups |
| Supply chain | Lockfiles, Dependabot, SHA pinning, Trivy, SBOM (M3), image pull by digest |
| Account takeover | Breached-password check (k-anonymity), login anomaly alerts, email on new device |

## 11. Backup and restore

- **PostgreSQL:**
  - pgBackRest: weekly full, daily diff, continuous WAL.
  - `repo1` on US, `repo2` on R2, AES-256 encrypted.
  - Retention of 4 weeks is sized against the free 10 GB of R2; monitor usage.
- **Redis:** AOF everysec. Redis is never the source of truth.
- **Configs and media:** restic to US and R2.
- **Vault private keys:** stored offline (age), separately from the data backups.
- **Drills:**
  - Monthly automated restore on US into a scratch PG, then integrity queries and a test decrypt with a *drill* key ID. Alert on failure.
  - Quarterly manual failover rehearsal (promote the US replica, switch the tunnel).
- **Targets:** RPO ≤ 5 min (WAL), RTO ≤ 2 h.

## 12. i18n and mobile

- **Locales:** `en` is the source; `zh-CN` is required in the same PR. URL strategy is `prefix_except_default` (`/zh/...`).
- **Translatable content:** `title_i18n` / `desc_i18n` jsonb; the API falls back to `en`.
- **Formatting:** `Intl` for numbers and currency; times stored in UTC and shown in the user's time zone.
- **Mobile first:** 375 px design baseline, Vant tabbar layout (Home / Search / Messages / Orders / Me), safe-area insets, initial JS ≤150 KB gzip, self-hosted fonts and assets.

## 13. CI/CD

| Workflow | Trigger | What it runs |
|---|---|---|
| `ci.yml` | push / PR | Ruff → pytest → Bandit → pip-audit (runtime deps block the build) → Semgrep → gitleaks → Docker build → Trivy → push to GHCR (`main` only) |
| `codeql.yml` | when `ENABLE_CODEQL=true` | CodeQL (free on public repos; private repos need GitHub Code Security) |
| Dependabot | weekly | pip, actions, docker |
| `deploy.yml` (M1) | `workflow_dispatch` on `main` | Ansible over WireGuard, pull by image digest, Alembic migrate, health check, auto-rollback to the previous digest |

## 14. Decisions (ADR log)

| # | Decision | Why | Consequence |
|---|---|---|---|
| ADR-001 | Build the core in FastAPI (Solution A), not on Dujiao-Next or Chatwoot | Plaintext card keys and open races in Dujiao-Next; Chatwoot can't do customer voice, WebRTC or quote cards; negotiation must be one transactional domain | More code; mitigated by CI gates and agents |
| ADR-002 | Asymmetric (X25519) card vault, sealing only in the API | A compromised web tier cannot read stock | Delivery worker isolation is mandatory |
| ADR-003 | Two entry paths (Cloudflare tunnel + CN edge) | Cloudflare Free is slow in CN; a grey-cloud-only setup is DDoS-fragile | Two hostnames; the frontend handles switching |
| ADR-004 | No business data or keys in mainland China | Legal (PRC USDT rules), seizure risk, PIPL | Tencent GZ is a probe only |
| ADR-005 | Centrifugo for realtime, PG as source of truth | Fallback transports, presence and recovery for free; Apache-2.0 | One more service on the edge |
| ADR-006 | BEpusdt as a separate process | GPL isolation; non-custodial | Must harden the private network |
| ADR-007 | All Actions SHA-pinned | trivy-action tag hijack, CVE-2026-33634 | Dependabot bumps the SHAs |
| ADR-008 | Repo private by default; CodeQL behind a variable | Avoid publishing security design before launch; CodeQL is paid on private repos | Semgrep and Bandit cover SAST for free |
