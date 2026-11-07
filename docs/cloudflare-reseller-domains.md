# Cloudflare plan: reseller subdomains + custom domains (PROPOSAL — not applied)

Status: waiting for owner approval. Agents do not apply Cloudflare changes. Current state was read
(read-only) on 2026-11-07: origin rules + config rules exist for `shop.kesiv.com` (c4 :8088, strict)
and `demo.kesiv.com` (demo box :18888, flexible); no wildcard record; Custom Hostnames list works
(0 hostnames), fallback origin not set.

## Phase A — demo (now)

| # | Change | Value | Why |
|---|---|---|---|
| A1 | DNS | `A  *.kesiv.com → 107.173.10.79`, proxied | `<slug>.kesiv.com` reaches the demo box. Explicit records (shop, demo, flux, tools, iij…) keep winning over the wildcard. Universal SSL covers `*.kesiv.com` (one level). |
| A2 | Origin rule | `(http.host wildcard "*.kesiv.com" and not http.host in {NAMED})` → port 18888 | Route storefront subdomains to the demo stack. |
| A3 | Config rule | same expression → SSL **flexible** | Demo origin is plain HTTP on :18888 (same as `demo.kesiv.com` today). |
| A4 | DNS | `A  stores.kesiv.com → 107.173.10.79`, proxied | Fallback origin for custom hostnames. |
| A5 | Custom Hostnames | fallback origin = `stores.kesiv.com` | Owner enables "Cloudflare for SaaS" (Free: 100 hostnames) in the dashboard first. |
| A6 | Origin + config rule | `(not http.host wildcard "*.kesiv.com" and http.host ne "kesiv.com")` → port 18888, SSL flexible | Custom hostnames (e.g. `shop.reseller.com`) to the demo stack. |
| A7 | API token (new) | Zone kesiv.com → **SSL and Certificates: Edit** only | The app creates/polls/deletes custom hostnames. Separate from the broad ops token; stored as a tenant secret, never in git. |

`{NAMED}` = every existing explicit host: `shop demo flux tools api delux cpalos www stores iij iijweb
iijtestcdndns iijtestonlydns softbank softbankweb racknerd vps dmit dmitipv6 666clouds akile
bandwagonhost bytevirt greencloudvps greenvps …` (generated from the DNS list when applying). The app
also refuses reseller slugs that equal a named host or a reserved word (`www`, `admin`, `api`,
`stores`, `mail`, …).

Reseller flow (custom domain): Reseller center → "Add domain" → API creates the custom hostname
(`ssl.method=txt` or http) → page shows `CNAME shop.their.com → stores.kesiv.com` + TXT records →
status polled → active. Storefront resolution checks `reseller_domains` (single function in
`app/core/storefront.py`).

## Phase B — production (later, owner runs)

- Point `*.kesiv.com` and `stores.kesiv.com` to c4 (141.98.198.150); origin rules → :8088; SSL **strict**
  (Origin CA cert must then cover `*.kesiv.com` + `stores.kesiv.com`; custom hostnames use the
  fallback origin's cert). Demo storefronts then move to query mode (`?store=slug`) only.
- Authenticated Origin Pulls already used for `shop.kesiv.com` → extend to the wildcard + fallback.

## Rollback
Delete A1–A6 (DNS records + rules) and the custom hostnames; storefronts keep working through
`?store=slug` (`resellers.link_mode = query`).

## Owner steps for the app side (v5.2, implemented)

1. Cloudflare dashboard: enable Cloudflare for SaaS and set the fallback origin (A4/A5 above).
2. Create the API token (A7) and the zone id of `kesiv.com`.
3. Admin -> Settings -> Resellers -> "Custom domains": paste the zone id, check the fallback origin
   (`stores.kesiv.com`), paste the token (stored as the sealed tenant secret
   `cloudflare_saas_token`, shown masked), enable the switch. Until all three are set the reseller
   center hides the card and `POST /reseller/domains` answers 409 `custom_domains_unavailable`.
4. Caddy: demo (`SITE_ADDRESS=:80`) already accepts any Host. On a domain-based production
   Caddyfile uncomment the `http://` catch-all block at the bottom of `deploy/Caddyfile`
   (no certificates are issued for it; Cloudflare holds the customer cert). With SSL **strict**,
   add an origin rule overriding SNI to `stores.kesiv.com`.
5. Reserved subdomains: `resellers.reserved_slugs` is prefilled with the named hosts; add any new
   explicit DNS host there (a fixed built-in set is always reserved too).
Reseller flow: Reseller center -> Settings -> "Your own domain" -> add hostname -> add the shown
CNAME/TXT records -> "Check again" until Active. Admin can force-remove under Resellers.
