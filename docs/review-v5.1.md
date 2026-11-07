# Review v5.1 — owner feedback on the v5 demo (2026-11-06)

Rules: AGENTS.md §7 (2 GB box: ≤ 2 agents, targeted local tests, full suite in CI). Build on
`main`. One implementer at a time, in this order. Each item → PR, CI green, merge, demo update.

## Diagnosis (verified 2026-11-06)

- **Order GPBDOMJNZD3I5 shows "Kesiv" and no +10 %**: it was placed on the main store
  (`orders.reseller_id IS NULL`, markup 0). The storefront is only active on `?store=<slug>`
  (sessionStorage, per tab) or on `<slug>.kesiv.com`. Store `sdf09fa70` is active at 10 %.
  → UX gap, not a pricing bug: see §2.
- **`http://slug.kesiv.com` → 503**: there is no DNS record for `*.kesiv.com`. The 503 page is
  generated on the visitor's side (local proxy/browser), not by Cloudflare or our origin. Even with
  DNS, the demo serves only `demo.kesiv.com` (origin rule → :18888). → §3.
- **Staff reprice "not allowed" after a promo**: intentional v5 audit fix (F1) — repricing an
  order with a promo/markup is refused (409). Buyers can never change prices. → §1 makes staff
  reprice work.

## 1. Staff reprice on promo / reseller orders (P1)
`PATCH /admin/orders/{id}/price` on a pending order with a promo or reseller markup: allowed.
Staff enter the **new final subtotal**; the promo is removed (redemption released, discount 0)
and the reseller markup is recomputed as `min(old markup, new subtotal − base cost floor)` or 0
when staff tick "remove reseller markup"; fee recomputed; checkout members refused (must cancel
checkout) with a clear message. Admin UI: show the current breakdown (subtotal, discount, markup,
fee) and the result preview; error codes translated. Buyer sees a system chat message with the
new price. Tests + payment-auditor pass.

## 2. Storefront clarity (P1)
- Reseller center "store URL": while subdomains aren't live, show `https://demo.kesiv.com/?store=<slug>`
  (built from `/site` base URL when `resellers.base_domain` host has no DNS → setting
  `resellers.link_mode: subdomain|query`, default `query` on demo).
- Storefront is remembered per browser (localStorage, 30 days, cleared by `?store=`-less visit
  to `/?store=off`), and order/checkout pages of a storefront order always render that store's
  branding (order payload has `reseller.store_name`).
- Admin order list/detail: "Store" badge already exists — verify it shows.

## 3. Reseller subdomains + own domains (P2, needs Cloudflare changes ⚠ show the owner first)
- Demo now: proxied wildcard `A *.kesiv.com → 107.173.10.79` + config rule (SSL flexible) +
  origin rule (port 18888) for `http.host wildcard "*.kesiv.com" and not http.host in {named
  hosts}`; later switch to prod. Universal SSL covers one level. App: host `<slug>.kesiv.com`
  already resolves the storefront.
- **Own domains (owner wants this)**: Cloudflare for SaaS custom hostnames (free up to 100 on
  Free plan; owner must enable "Custom Hostnames" in the CF dashboard once and set fallback origin
  `stores.kesiv.com`). Reseller center: add domain → API creates the custom hostname → show
  `CNAME shop.their.com → stores.kesiv.com` + TXT validation → poll status → active. New table
  `reseller_domains(reseller_id, hostname unique, cf_id, status, verified_at)`; storefront
  resolution checks it (single function in `app/core/storefront.py`). Token perms: existing
  "SSL and Certificates: Edit" covers custom hostnames — verify.

## 4. Payment providers like sub2api + USDT (P1)
- Read sub2api's payment design first (`gh api repos/Wei-Shaw/sub2api/contents/...`:
  backend payment provider instances, `payment_provider_instances` table, frontend
  `AdminPaymentPlansView`/settings payment tab). Mirror the model: **provider instances**
  (`type: epay | usdt | ...`, name, enabled, config, secret refs in `tenant_secrets`, fee
  permille, min/max, sort, load-balance) and buyer-visible **payment methods** (alipay, wxpay,
  usdt) mapped to instances.
- `epay` type covers EPay-protocol gateways: ZPay (z-pay.cn), 彩虹易支付, 蓝兔支付, 虎皮椒
  (check which are EPay-compatible; 虎皮椒/xunhupay has its own API → separate type later).
  Existing ZPay env config becomes the first instance (migration, env fallback kept).
- **USDT first** (no merchant approval): self-hosted **BEpusdt** (docker, ~30 MB, EPay-compatible
  `submit.php` + notify) behind our existing EPay verifier, as instance type `usdt` with networks
  **Arbitrum One USDT** and **X Layer USDT** (both ~$0.01 fees; X Layer = OKX L2, free OKX
  withdrawals). Verify both networks are supported by the current BEpusdt release; TRC20 as
  optional third. Rate CNY→USDT from BEpusdt (OKX rate) with a configurable markup.
  ⚠ Owner provides: one EVM receiving address (works for both networks).
- Ledger: `gateway_clearing:<instance>`; fees per instance. payment-auditor review mandatory.

## 5. Block IPs / users (P2)
- **IPs → real Cloudflare block page** (genuine Ray ID; no fake CF page): admin "Blocked IPs"
  (IP/CIDR, note, expiry) → API syncs to a Cloudflare IP List used by one WAF custom rule
  (`ip.src in $kesiv_blocked` → block), or zone IP Access Rules. ⚠ Owner adds token permission
  "Account Filter Lists: Edit" (lists) or "Zone → Firewall Services: Edit" (access rules).
  Fallback when no permission: app middleware 403 with our own "Access denied" page.
- **Users**: ban/unban buyer (and staff by admin) → sessions revoked, login refused, own page
  "Your account has been suspended". Audited.

## 6. Product create: parse + auto-delivery (P1)
The importer exists (Products → "Import"), but the owner looked inside **New product**. Add an
"Auto-fill from URL / pasted page" box at the top of ProductDialog (same endpoints), and allow
choosing **auto delivery** there with an inline card-import step (paste codes) so a product with
stock is created in one flow. Imported default stays manual.

## Owner inputs
Received 2026-11-07 (public addresses only; no secrets in git):
- **USDT receiving (§4):** EVM `0xe2dc319ed904b2f798f262e3274b0f0c60e0653c` for **Arbitrum One** and
  **X Layer** (OKX shows the X Layer form as `XKOe2dc…0653c`; the `XKO` prefix is only an OKX-app
  label, so on-chain it is the same `0x…` address). **TRON (TRC20):** `TQkeoASc6X5itH4MyPZPPCfLM4M62cvMu5`.
- **Telegram:** bot `@shopkesivbot`, owner `@signalstacker` (chat id 8847311752). Token is in
  `/root/.secrets/telegram.env` (not in git). No spec item uses it yet; candidate for staff
  notifications (new order / payment / CS message) in a later item.
- **Cloudflare for SaaS:** the owner is activating it (100 free custom hostnames). Read-only check
  2026-11-07: the token is active and `GET custom_hostnames` works (0 hostnames); the fallback origin
  is **not set**. Write permission isn't verified (that would need a write). Check it again with the
  prod token before §3 goes to prod. **Agents don't change Cloudflare settings without the owner.**

Still needed: CF token permission for IP lists/access rules (§5).
