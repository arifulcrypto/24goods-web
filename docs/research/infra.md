# Research: Infrastructure, Cloudflare & China access, CI/CD, frontend

> Researched 2026-11-05 by a research sub-agent. **[V]** = verified against official docs / npm / PyPI / GitHub API. **[A]** = anecdotal (blogs, forums). **[I]** = inference — test before relying on it.

## 0. Legal note — read first

- **[V via press]** PBoC 2021 notice: virtual-currency business is "illegal financial activity". **Yinfa [2026] No. 42 (6 Feb 2026)** restates that trading/facilitating **USDT** is illegal and requires internet companies to block such platforms ([KuCoin News](https://www.kucoin.com/news/flash/china-expands-crypto-crackdown-to-stablecoins-and-asset-tokenization), [Crypto Valley Journal](https://cryptovalleyjournal.com/legal-and-compliance/china-bans-yuan-stablecoins-and-rwa-tokenization-in-new-crypto-regulation/)). Primary text not fetched.
- **[I]** Accepting USDT from mainland users exposes the operator regardless of server location, and the domain can be GFW-blocked. **Put no business data, keys or payment logic inside mainland China (Tencent GZ).** Get legal advice. Design for domain rotation.

## 1. Cloudflare and mainland China

- **[A]** Free/Pro: CN users are often routed to HK/JP/KR or the **US West Coast**; 200–300 ms with loss, evening spikes >1000 ms on China Unicom ([cdnchart](https://www.cdnchart.com/en/blog/cloudflare-cdn-review-free-plan-mainland-china), [CF community](https://community.cloudflare.com/t/very-high-latency-issue-from-china-unicom-to-cloudflare-free-plan-ip-range/846064)).
- **[V]** Cloudflare China Network = Enterprise + extra subscription + **ICP filing** + JD Cloud review ([FAQ](https://developers.cloudflare.com/china-network/faq/)) → not available to you.
- **[V] Turnstile:** "Turnstile is not available within Mainland China"; global zones with CN visitors may also have issues ([available products](https://developers.cloudflare.com/china-network/reference/available-products/)). → Never make Turnstile a hard dependency for CN users; fall back to self-hosted proof-of-work captcha ([ALTCHA](https://github.com/altcha-org/altcha)) + server-side rate limits. Never reCAPTCHA.
- **Split routing:** CF Load Balancing geo-steering is paid. Free alternatives: DNSPod / Alibaba / Huawei DNS 境内/境外 lines (but you lose CF-managed DNS). **Simplest: use your second domain** — `example.com` proxied by Cloudflare for international users, `cn.example2.com` grey-clouded straight to SoftBank for CN users; app suggests the CN entry by geo/latency probe; rotatable if attacked or blocked.
- **"优选 IP" (hand-picked CF IPs, [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest)):** undocumented, can break anytime; third-party "optimized CNAME" domains are a supply-chain/MITM risk. Experiment only; never your only CN path.
- **R2 [V]:** `r2.dev` is rate-limited and not for production → custom domain. **Presigned URLs only work on `<account>.r2.cloudflarestorage.com`, not custom domains**; max expiry 7 days ([presigned](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)). CN speed to R2 ≈ CF speed → for CN users proxy/cache R2 via Caddy on SoftBank. **Never put card keys or delivery files in a public bucket.**

## 2. DDoS / CC with an exposed origin

- **[V]** CF Free: 1 rate-limiting rule (10 s period) ([docs](https://developers.cloudflare.com/waf/rate-limiting-rules/)). CrowdSec v1.8.2; Caddy bouncer with AppSec WAF: [hslatman/caddy-crowdsec-bouncer](https://github.com/hslatman/caddy-crowdsec-bouncer).
- **[I]** A grey-clouded IP gets only the provider's protection; most SoftBank-route JP VPS resellers **null-route** big attacks. Ask your provider's threshold. Assume the CN-entry IP will be found.

**Pattern: "default safe, CN fast, switchable"**
1. Main domain proxied by CF; origins accept 80/443 **only from [CF IP ranges](https://www.cloudflare.com/ips/)** (nftables, cron refresh) + Authenticated Origin Pulls, or `cloudflared` Tunnel (no inbound ports). Fresh never-exposed IPs; never send mail from them.
2. CN entry = separate SoftBank IP under the second domain, grey-clouded, **edge only**: Caddy + CrowdSec bouncer/AppSec + per-route rate limits; nftables conn limits; SSH via WireGuard only; reverse-proxies to the app tier over WireGuard; **no DB, no secrets**.
3. Kill switch: under attack flip CN entry to orange-cloud (slower but protected) or rotate IP/hostname; TTL 60–300 s.
4. App-level everywhere: Redis token buckets, PoW captcha on expensive endpoints, idempotency keys, body-size limits.

## 3. Reseller custom domains

- **Caddy on-demand TLS** ([docs](https://caddyserver.com/docs/automatic-https#on-demand-tls)) **[V]**: must be restricted via the `ask` endpoint (FastAPI checks domain registered + TXT/CNAME verified + active). Let's Encrypt: 300 new orders/3 h/account, 50 certs/registered domain/7 days, ARI renewals exempt ([limits](https://letsencrypt.org/docs/rate-limits/)).
- **Cloudflare for SaaS** **[V]**: available on Free — **100 custom hostnames included, $0.10 each after**, up to 50k ([plans](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/plans/)); no wildcard/apex proxying on Free.
- **Fit [I]:** international reseller storefronts → CF for SaaS (origin hidden, DDoS + certs handled). CN-facing reseller domains → Caddy on-demand TLS on the SoftBank edge. Both share one `custom_domains` table.

## 4. Server allocation (agent proposal — see architecture.md for final)

| Host | Role |
|---|---|
| SoftBank JP | CN edge: Caddy + CrowdSec, on-demand TLS, CN image cache, one stateless API replica. No DB, no KEK. |
| IIJ JP | Core: PostgreSQL primary + Redis + main API workers; CF-proxied origin (CF-IP firewall / Tunnel). |
| US UT | DR + ops: PG async replica, pgBackRest repo, monitoring, OpenBao (KEK/Transit) on WireGuard only. |
| Tencent GZ | Only a CN-vantage synthetic probe (reachability/latency/blocking alerts). No user data. |

- Private network: plain **WireGuard** mesh, or [Headscale](https://github.com/juanfont/headscale) v0.29.7 with self-hosted DERP (Tailscale's DERPs are poor from CN).
- **Backups:** [pgBackRest](https://pgbackrest.org) 2.60.0 → repo1 US (posix), repo2 R2 (S3), `repo-cipher-type=aes-256-cbc`; weekly full + daily diff + WAL. 10 GB R2 fills fast — set retention. Redis AOF everysec, rebuildable only. Files: [restic](https://restic.net) 0.19.1. **Monthly automated restore drill** on US + quarterly failover rehearsal.

## 5. Secrets & card-key encryption

- App-layer envelope encryption: per-record DEK, AES-256-GCM, AAD bound to the record; DEK wrapped by KEK in [OpenBao](https://github.com/openbao/openbao) Transit (v2.7.2) or a minimal KEK service; unwrap only in the delivery worker; rotate + rewrap.
- **pgcrypto rejected:** the key travels in SQL (logs, `pg_stat_statements`, replicas); app-layer keeps plaintext and keys out of PG and all backups.
- Config secrets: [SOPS](https://github.com/getsops/sops) 3.13.3 + [age](https://github.com/FiloSottile/age).

## 6. GitHub CI/CD

**Pin every third-party action to a full commit SHA.** In March 2026, `aquasecurity/trivy-action` had ~75 tags force-pushed to credential-stealing code (**CVE-2026-33634**, CISA KEV) ([CIRCL](https://cve.circl.lu/cve/CVE-2026-33634)).

| Purpose | Tool (latest, verified) |
|---|---|
| Checkout / setup | `actions/checkout@v7` (7.0.1), `actions/setup-python@v7`, `astral-sh/setup-uv@v10` |
| SAST | CodeQL `github/codeql-action@v4` (v3 deprecated Dec 2026), Bandit 1.9.4, Semgrep CE |
| SCA | `pypa/gh-action-pip-audit@v1.1.0`, Dependabot (pip, npm, actions, docker) |
| Secrets | GitHub secret scanning + push protection; `gitleaks/gitleaks-action@v3` (**v2 stopped working 2026-09-16**; org repos need free licence) |
| Container | buildx v4, build-push v7, login v4 → GHCR; Trivy `trivy-action@v0.36.0` SHA-pinned |
| SBOM / provenance | `anchore/sbom-action`, `actions/attest-build-provenance@v4` |
| DAST | `zaproxy/action-baseline@v0.15.0` against staging |
| Deploy | Environments, pull by image digest, Ansible over WireGuard |

**Plan caveats [V]:**
- **CodeQL and secret scanning on private repos need paid GitHub Code Security ($30/committer/mo) / Secret Protection ($19)** ([changelog](https://github.blog/changelog/2025-03-04-introducing-github-secret-protection-and-github-code-security/)). Free on public repos.
- **Required reviewers on private-repo environments need GitHub Enterprise** ([docs](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)). Workaround: manual `workflow_dispatch` deploy restricted to `main` + branch protection.

## 7. Frontend (mobile-first, EN/ZH)

Versions (npm, 2026-11-05): nuxt 4.6.4, @nuxtjs/i18n 10.6.0, vue 3.5.44, vant 4.10.2; next 16.4.5 + next-intl 4.14.9; @sveltejs/kit 3.1.1 + paraglide-js 3.1.0.

**Recommendation: [Nuxt 4](https://nuxt.com) + [@nuxtjs/i18n](https://i18n.nuxtjs.org) + [Vant 4](https://vant-ui.github.io/vant/)** — Vant (Youzan) is purpose-built for Chinese mobile commerce (tabbar, pull-refresh, infinite list, SKU sheet, action sheet) with built-in `en-US`/`zh-CN`; @nuxtjs/i18n gives `prefix_except_default` (EN default), hreflang and lazy locales; hybrid `routeRules` = SSR/SWR for listing/product pages, `ssr:false` for account/checkout/seller console. Baidu SEO is moot without ICP; optimise Google/Bing + raw CN speed.

**China speed rules:** self-host all fonts/assets, no Google anything, no runtime CDN (jsDelivr unreliable in CN), Brotli + HTTP/3 on Caddy, initial JS ≤150 KB gz, Vant on-demand imports.

## Open items to verify yourself

Real CN→SoftBank vs CN→Cloudflare latency per carrier (Tencent GZ probe or itdog.cn) · SoftBank provider's blackhole threshold · DNSPod line options · OpenBao unseal strategy · **legal advice on USDT / mainland users**.
