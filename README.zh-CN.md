# 24goods（Kesiv）

[English](README.md) · **简体中文**

**安全优先、移动优先、中英双语的虚拟商品与数字服务交易平台。**
买家可以与卖家聊天议价、获取专属报价、在聊天中直接付款，并自动（卡密/兑换码）或人工收货。
代理商可以开设白标店铺并设置自己的加价。

- **在线演示：** https://demo.kesiv.com（模拟支付，数据会不定期重置）
- **正式站：** https://shop.kesiv.com
- **当前阶段：** MVP，正迈向 M1 正式版，详见 [docs/progress.md](docs/progress.md)。

## 功能

| 模块 | 内容 |
|---|---|
| 商城 | 移动优先的买家端（Vue 3 + Vant），中英文切换，搜索、分类、商品页、订单查询 |
| 发货 | 加密卡密库自动发货；人工发货（文字 + 附件）；售后期 |
| 聊天与报价 | 闲鱼式买家 ↔ 客服聊天，支持图片、语音消息、报价卡片、聊天内付款，客服工作台 |
| 支付 | 易支付协议通道（ZPay / 支付宝）；自建 BEpusdt 收 USDT（Arbitrum One、X Layer、TRC20）；多个易支付通道按优先级 + 粘性故障切换；按通道设置手续费与开关 |
| 代理商 | 白标店铺（`?store=slug`、子域名、已验证的自定义域名）、加价、收益账本 |
| 管理后台 | PC 端（Vue 3 + Element Plus）：商品、按链接/页面导入商品、库存、订单、改价、优惠券、账本、审计日志、设置 |
| 安全 | 卡密非对称信封加密、PostgreSQL 行级安全按租户隔离、支付回调多重校验、Turnstile、限流、客服 IP 白名单 |

## 架构

```
浏览器 ──► Cloudflare ──► Caddy（静态单页应用 + 反向代理）
                            ├─► api        FastAPI 模块化单体（REST + SSE）
                            ├─► delivery   独立发货进程：消费 outbox，唯一持有卡密私钥，无外网出口
                            ├─► PostgreSQL（行级安全、按角色授权、只追加账本）
                            └─► Redis     （会话、限流、发布订阅）
```

- 后端：Python 3.12、FastAPI、SQLAlchemy 2（异步）、Alembic、PostgreSQL 17、Redis 7。
- 前端：pnpm 工作区，两个纯静态 Vite 应用（`apps/web`、`apps/admin`）+ `packages/shared`，运行时不需要 Node。
- 设计决策以 ADR 形式记录在 [docs/architecture.md](docs/architecture.md)。

## 安全模型（摘要）

1. 卡密从不以明文存储、记录或返回；只有发货进程能解密。
2. 支付回调只是提示：签名 → 商户号 → 金额精确比对 → 状态 → 服务端反查 → 行锁 → 交易号唯一 → outbox。
3. 价格永远以服务端为准，每次改价都有审计记录。
4. 仓储层与 PostgreSQL 行级安全双重租户隔离。
5. 密钥不进 git；上传文件一律视为不可信；运行时不加载第三方 CDN 资源。

完整规则见 [AGENTS.md §1](AGENTS.md)。扫描不等于审计：接入真实资金前必须做独立安全评估/渗透测试。

## 快速开始（开发环境）

环境要求：Linux、Docker、Python ≥ 3.10、Node 22 + pnpm（corepack）。2 GB 内存的机器即可运行。

```bash
git clone https://github.com/arifulcrypto/24goods-web.git && cd 24goods-web
scripts/dev.sh setup     # 容器（PostgreSQL、Redis）、虚拟环境、pnpm install，密钥放在 .dev/
scripts/dev.sh up        # API + 发货进程 + Vite 开发服务器（热更新 + 模拟数据）
scripts/dev.sh creds     # 演示账号（买家 / 客服 / 管理员）
scripts/dev.sh down      # 不用时关闭
```

端口与从本地电脑访问的 SSH 隧道说明见 [docs/dev-setup.md](docs/dev-setup.md)。

**用 Docker 跑完整栈（模拟数据，与演示站相同）：**

```bash
PUBLIC_BASE_URL=http://127.0.0.1:8080 PUBLISH_HTTP=8080 \
API_IMAGE=ghcr.io/arifulcrypto/goods24-api:main WEB_IMAGE=ghcr.io/arifulcrypto/goods24-web:main \
  ./deploy/bootstrap.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile demo up -d --wait
cat deploy/secrets/demo-credentials.txt
python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env
```

## 测试

```bash
scripts/dev.sh test                       # ruff + pytest（使用开发容器）+ 前端 lint/类型检查/单测
cd backend && pytest -q tests/test_x.py   # 小内存机器上只跑改动相关的测试
cd frontend && pnpm lint && pnpm typecheck && pnpm test
```

完整测试在每个 PR 的 GitHub Actions 中运行（见 [AGENTS.md §3、§7](AGENTS.md)）。

## 部署

| 环境 | 主机 | 方式 |
|---|---|---|
| 开发 | 开发机上的本仓库 | `scripts/dev.sh` |
| 演示 | `demo.kesiv.com` | 拉取式部署：CI 通过后，`deploy/demo-update.sh`（systemd 定时器）从 GHCR 拉取 `:main` 镜像 |
| 生产 | `shop.kesiv.com`（日本 IIJ） | 在开发机执行：`scripts/prod.sh sync`、`scripts/prod.sh deploy`、`scripts/prod.sh smoke`，详见 [docs/production.md](docs/production.md) |

镜像只有在全栈 E2E 冒烟测试通过后才会构建、经 Trivy 扫描并推送到 GHCR。
回滚 = `git revert <sha>` 后重新部署上一个镜像 digest。密钥保存在 git 忽略的环境文件中
（`deploy/env/<服务>.env`，每个容器一份，最小权限），绝不进仓库。

## CI/CD

后端（Ruff、基于 PostgreSQL + Redis 的 pytest、Bandit、pip-audit）· 前端（lint、vue-tsc、vitest、构建 + 体积预算）·
Semgrep · gitleaks · 镜像构建 + Trivy · 全栈 E2E 冒烟测试 · 推送 GHCR · 演示站拉取式部署。
所有 GitHub Action 均固定到 commit SHA。

## 路线图与待解决问题

当前工作与未完成事项见 [docs/progress.md](docs/progress.md) 和最新需求文档
[docs/review-v5.2.md](docs/review-v5.2.md)。重点：

- 通过 BEpusdt 收 USDT（Arbitrum One、X Layer、TRC20），以及支付方式开关
- Telegram 客服通知
- 封禁 IP / 用户
- 代理商子域名与自定义域名（Cloudflare for SaaS）
- 面向 Agent 的 API（带权限范围的令牌）批量上架、SEO（爬虫可读 HTML、sitemap、robots）、粘贴图片上传
- 接入真实资金前：独立安全评估 / 渗透测试（M1 验收门槛）

## 文档

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 人与 AI Agent 共同遵守的规则：安全不变量、命令、流程、资源预算 |
| [docs/architecture.md](docs/architecture.md) | 拓扑、卡密库、支付流程、议价、安全矩阵、ADR |
| [docs/api-contract.md](docs/api-contract.md) | REST + SSE 接口约定 |
| [docs/progress.md](docs/progress.md) | 当前状态、已完成、下一步 |
| [docs/dev-setup.md](docs/dev-setup.md) · [docs/production.md](docs/production.md) | 开发环境 · 生产运维 |
| [docs/analysis.md](docs/analysis.md) · [docs/research/](docs/research/) | 技术选型理由、带出处的调研 |

## 法律提示

各国对数字商品和加密货币支付的监管不同。在中国大陆，USDT 相关业务属于违法活动；
本项目不在大陆托管任何数据、密钥或支付逻辑。请针对你的市场咨询律师。
