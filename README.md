# 24goods

**A security-first, mobile-first, bilingual (EN / 简体中文) marketplace for virtual goods and digital services.** It supports manual C2C delivery, automatic card-key/API delivery, Xianyu-style chat → custom quote → in-chat payment, USDT and EPay channels, and reseller white-label stores.

> Status: **MVP-0: runnable demo with mock data** (buyer app, staff console, chat → quote → pay → auto/manual delivery). See [docs/progress.md](docs/progress.md).

## Read first

| Doc | What it covers |
|---|---|
| [docs/review-v2.md](docs/review-v2.md) | **Current decisions** (v2): frontend, chat, Chatwoot, key isolation, ZPay, Caddy ask, tenancy matrix, timeline |
| [docs/api-contract.md](docs/api-contract.md) | REST + SSE contract shared by backend and frontend |
| [docs/analysis.md](docs/analysis.md) | **The evaluation**: Dujiao-Next / Chatwoot / Saleor / Medusa / Bagisto / acg-faka / Sub2API compared; three solutions; recommendation; milestone priorities |
| [docs/architecture.md](docs/architecture.md) | Server topology, card-key anti-theft design, payment pipeline, negotiation system, security matrix, ADRs |
| [docs/progress.md](docs/progress.md) | Done / next / open questions |
| [AGENTS.md](AGENTS.md) | Rules for humans and AI agents: security invariants, validation commands, workflow |
| [docs/research/](docs/research/) | Raw research with sources and verified/inferred markers |

## Key findings

- **Dujiao-Next, dujiaoka and acg-faka all store card keys in plaintext** (verified in source). This repo's vault uses **asymmetric envelope encryption**, so the public API can add stock but can never read it.
- **Chatwoot isn't suitable for buyer↔seller chat.** Customers can't record voice messages, calls are paid and Twilio-only, and there are no real quote cards. **Crisp may be blocked in China.** → Chat is built into the app (FastAPI + SSE, PostgreSQL history) with a staff Inbox; voice clips yes, voice calls dropped.
- **Cloudflare Free is slow from mainland China, and Turnstile is unreliable there.** → Use two entry paths: a Cloudflare tunnel for international users, and a SoftBank JP edge that holds no data for CN users.
- **Legal:** PRC rules treat USDT business as illegal. Host nothing in mainland China, and get legal advice.

## 中文摘要

**结论：** 推荐“方案 A：核心自研、通用组件复用”。

**技术组合：**
- 核心：FastAPI + PostgreSQL + Redis
- 实时通讯：SSE + Redis（MVP），规模扩大后再引入 Centrifugo
- 语音：支持语音消息（≤60 秒）；语音通话已移出路线图
- 人民币通道：ZPay 支付宝 V1（易支付协议），USDT（BEpusdt）放到第二阶段
- 证书与自定义域名：Caddy
- 前端：两个纯静态 Vite 单页应用：买家端 Vue 3 + Vant 4（移动优先），客服/管理端 Vue 3 + Element Plus（PC），服务器不运行 Node

**为什么不直接用开源方案：**
- 独角数卡 Next 的卡密为**明文存储**，并有未修复的退款/发货竞态问题，只作参考或过渡方案。
- Chatwoot 不支持买家语音消息，也没有 WebRTC 通话和报价卡片，不适合做买卖双方私聊。

**服务器分工：**
- 软银 JP：面向国内用户的入口边缘节点，不存数据。
- IIJ JP：核心应用与数据库。
- 美国 UT：灾备、备份与监控。
- 腾讯广州（未备案）：仅作国内探测节点，不放任何业务数据。

**合规提醒：** USDT 在中国大陆属于违法金融活动，请先咨询律师。

详见 [docs/analysis.md](docs/analysis.md) 与 [docs/architecture.md](docs/architecture.md)。

## Quick start

**Development (hot reload, mock data):** `scripts/dev.sh setup && scripts/dev.sh up` — see [docs/dev-setup.md](docs/dev-setup.md) (SSH tunnel to open it from your PC).

```bash
# whole stack with mock data (needs Docker; ~600 MB RAM)
PUBLIC_BASE_URL=http://127.0.0.1:8080 PUBLISH_HTTP=8080 \
API_IMAGE=ghcr.io/arifulcrypto/goods24-api:main WEB_IMAGE=ghcr.io/arifulcrypto/goods24-web:main \
  ./deploy/bootstrap.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile demo up -d --wait
cat deploy/secrets/demo-credentials.txt      # buyer / agent / admin logins
python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env
```

Developing: see `AGENTS.md` §3 (backend tests need PG + Redis), `frontend/README.md`.

## CI/CD

backend (Ruff, pytest on PG + Redis, Bandit, pip-audit) · frontend (lint, vue-tsc, vitest, build + size budget) · Semgrep · gitleaks · images + Trivy · **full-stack E2E smoke** · push to GHCR · pull-based deploy to the demo host (`deploy/demo-update.sh`). All Actions are pinned to commit SHAs.
