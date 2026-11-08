# Issues #25–#30 implementation and evidence

Branch: `feat/issues-25-30-hardening`. Review: [draft PR #31](https://github.com/arifulcrypto/24goods-web/pull/31). English is primary; UI translations include zh-CN. No main push, merge, production deployment, real merchant charge or live-key configuration is part of this work.

## Implemented scope

| Issue | Implementation | Evidence / limits |
|---|---|---|
| #25 input/UX | Server-owned edit capability/config, optional clear, explicit disabled error, no fabricated fallback; Auto browser language; long-press/copy/shared pins/quoted drafts; sender recall <=180s; compression/network preferences/private thumbnail | Input remains plaintext in the order table under existing #8; recalled content already downloaded cannot be erased from another device. Shared pins are limited to three and shown for loaded messages. |
| #26 verified findings | Native BEpusdt non-200 failures preserve retries; sensitive chat-copy removal/legacy masking/purge; monotonic SSE checks/caps; atomic OTP consume; fail-closed TS action/host/config checks; upload admission before body buffering; PG-backed attempts | Reproduced OTP CI race corrected with one fixed-size constant-time MAC/count/consume Lua script. Native ACK contract is vendor-specific; do not copy HTTP behavior between adapters. |
| #27 payment design | Encrypted immutable EPay instance versions, durable reservations, uncertain-create hold, original-instance routing/requery/reconciliation, typed local QR/private page, optional initiation verification | Initial configurable method is Alipay EPay v1. `zpay_alipay` remains an internal compatible channel ID. Native RSA/Lantu/direct official Alipay/WeChat/QQ/bank adapters require their separate contracts and merchant approval. |
| #28 operations | Platform-admin read-only bounded overview/events, login and task heartbeat logging; secret-age reminder; encrypted whole-set/fenced backup with manifest and offsite receipt, separate vault recovery, safe recovery collector | Synthetic backup control-flow tests do not prove production recovery or age decryptability. Actual RPO/RTO, historical plaintext backup transition, retention/capacity/off-host alert/PITR/restore drills and host inventory must be established operationally before release. No root panel, scanner attack or vulnerable honeypot is deployed. |
| #29 services | Staff activation, IANA timezone/calendar/fixed periods, reason/version-bound edits, paid original order/once-only paid renewal, durable opt-in staff mail/TG reminders | No recurring debit. External sends are bounded at-least-once and may repeat with a stable ID after uncertain success. Current overdue notices are per entitlement/day; no combined digest. |
| #30 Xiaodou | Reviewed supplied v1.0.0 text import, English index, provenance/hashes, MD5 EPay form create/query/GET notify contract; encrypted admin configuration/rotation and local QR | Live credentials excluded. Supplier's native RSA contract is separate. Owner reports Alipay approval; mocks do not independently prove approval, honest settlement or ongoing authorization. |

## USDT rotation and trust

The existing BEpusdt console manages its supported receiving-wallet pools and networks. 24goods uses the returned receiving address per durable attempt, rather than hardcoding one wallet on the buyer page. New address creation can change while existing attempts retain their exact old receiving evidence. API token/base rotation retains encrypted original credentials for those attempts. Unknown creation without a returned trade ID requires manual gateway reconciliation; missing recovery evidence never implies unpaid.

The gateway is a trusted self-hosted integration, not an independent chain oracle. Do not enable an arbitrary third-party crypto endpoint as if it implemented BEpusdt. Check canonical deployment/package provenance, token contract, chain/finality, wallet ownership, scope and settlement independently, following the [Web3 research](research/web3-ops-risks-2026-11-08.md). A valid key/signature alone is insufficient. Rotation/real wallet management is an explicit operator step and was not performed here.

## Validation record

Local checks are targeted under the repository's shared resource lock. Full backend/SAST/SCA/frontend/E2E/image checks run in GitHub CI, not a duplicate local full suite. Initial CI discovered the OTP burst-consumption race; it was fixed instead of weakening the concurrent-success assertion. Individual test totals/last commit and review results are recorded in the PR when the final revision is checked.

Security/payment/mobile read-only review and a green final CI revision are required before the implementation is ready to merge. The owner explicitly requested an unmerged PR. Independent human security review/pentest and approved live provider/restore operations remain separate real-money release gates. This document does not certify the deployed host, absence of all bugs, DDoS immunity or upstream anonymity.
