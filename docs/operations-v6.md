# V6 operations and recovery runbook (unmerged implementation)

English is the source language. No production deployment or real charge was performed for this PR. The independent security review and human approval remain required.

## Encrypted complete backups

`deploy/backup.sh` replaces the plaintext component script previously embedded in `scripts/prod.sh`. It requires the **age** operator tool (new operational dependency, no application package), Docker, `flock`, `tar`, `sha256sum`, GNU coreutils and adequate local staging space. Install verified vendor packages/signatures using your OS policy; do not pipe unknown installation scripts into a shell. See [age upstream](https://github.com/FiloSottile/age).

Configure two **public recipient files outside Git**: `/opt/kesiv/backup-recipients.txt` for DB/media/provider/configuration and `/opt/kesiv/backup-vault-recipients.txt` for private delivery KEKs. Keep their private recovery identities independently/offline with different access policies. Losing the identities makes the corresponding backup unrecoverable. Retaining encrypted old gateway credentials also requires the original settings encryption key. Never rotate/decommission recovery keys without testing old data recovery.

The script takes a backup-specific lock, stops only previously running API/delivery/BEpusdt writers, captures a consistent PG dump, uploads, stopped BEpusdt data volume including SQLite journal/WAL files, configuration and separately encrypted private vault keys. Writers restart through an exit trap. This is a short **maintenance interruption**, not an online/PITR solution. Query and reconcile payments arriving during/after fencing before enabling delivery after a restore. Verify every writer is included before adding another process. Externally hosted gateways need their own recovery policy: the bundled volume backup cannot capture an unrelated remote service.

`BACKUP_GATEWAY_STATE=required` is the fail-closed default and requires `goods24_bepusdt_data`; use `disabled` only when no real USDT gateway is configured. Preserve the provider inventory as recovery evidence. Dumps stream into age; no final plaintext backups are published. Schema/release metadata and ciphertext hashes are not secret values.

Only a successfully resumed, hash-verified directory is atomically published with `COMPLETE`. Failed `.partial-*` sets are not restorable completion evidence. `scripts/prod.sh backup` copies one whole generation, verifies its manifest locally, then records `OFFSITE_VERIFIED`. There is deliberately no automatic pruning: adopt a tested retention policy only after offsite completion, capacity checks and key recovery have been demonstrated. Protect existing legacy plaintext backups during the transition; this PR neither deletes nor encrypts historical copies automatically.

Choose/record RPO and RTO. Daily PG logical dumps are full backups. A 15-minute RPO needs a separately configured/tested WAL/base-backup/PITR system. A successful script exit does not prove decryptability, an intact offsite failure domain or successful restoration. Cron installed by the old script is not automatically changed by this PR.

## Isolated restore evidence

1. Restore into a disposable isolated network with outbound mail/TG, gateway writes and delivery fenced. Record generation, schema, release digest, operator and start/end times.
2. Verify `MANIFEST.sha256`; decrypt with independently held identities; verify `pg_restore --list` and every required component. Rebuild roles/grants/RLS with the matching release and migration procedure. Restore provider state while stopped.
3. Test synthetic vault reveal with delivery-only credentials, private media ACLs, tenant RLS, balanced ledger, outbox uniqueness and interrupted delivery recovery. Never use a normal development environment for production secret-bearing dumps.
4. Reconcile all original payment attempts and any payments received after the restore point, including already fulfilled transactions, before releasing fulfilment. Ordinary notify replay/idempotency does not by itself prevent a second delivery from a restored older DB.
5. Record results and failures; only then write the UTC timestamp of the successful drill to `LAST_RESTORE_TEST`. Do not infer it from a backup date. Real merchant/chain settlement requires independent evidence; callback+query from one compromised provider are not independent verification.

## Read-only overview and logs

`/admin/operations` is platform-admin session only. It exposes bounded cached process/cgroup/filesystem readings and tenant queue summaries. `/proc/meminfo` and load are labelled host-visible; container RSS is not host RAM, cgroup limits can be unavailable and disk scope is the upload filesystem. Delivery heartbeat/schema/host inventory remain unknown unless separately supplied. No Docker/LXD socket, root command, writable host mount or delivery private key is added.

Optional `deploy/operations-status.py --backups <directory> --output <separate-directory>/status.json` publishes only marker timestamps/receipt flags; mount that **separate output directory**, read-only, at `/run/24goods-operations`. It does not certify a restore or need the API to read the backups. Collector deployment/cron are explicit operator steps and were not performed here. Missing/stale evidence must be investigated. Independent off-host heartbeat/pressure alerting and a tested PITR/retention policy remain production operational gates.

Operation/login/task/service events are escaped text, bounded metadata with UTC timestamps and unknown geolocation unless a trusted lookup is separately added. Retain existing audited financial state transitions and CSV injection protection. Proxy logs remove query strings and sensitive verification headers while preserving Caddy's default credential-header redaction ([Caddy documentation](https://caddyserver.com/docs/caddyfile/directives/log)). Browser agent and IP are hints, not proof of identity or an attack. Edge/WAF/provider network controls, quotas and off-host monitoring are needed for volumetric DDoS; application challenges do not replace them. A deliberately vulnerable honeypot is not deployed.

## Staff service periods

`/admin/services` tracks an original verified paid order, buyer, assigned active staff owner, explicit IANA business timezone, calendar-month or fixed-day policy, actual activation, grace, status and version. Payment alone does not activate it. Each renewal links a separate verified paid order and an explicit staff completion confirmation once; no automatic debit. Staff edits require an audit reason. Calendar months clamp month-end; fixed days are 24-hour intervals. Cancellation/refund must stop reminders; staff must confirm the business effect of refunds.

Email/TG are opt-in, environment-scoped and carry only safe references, expiry, environment, stable notification ID and an authenticated admin link. Durable reminder schedules deduplicate by entitlement/version/kind/channel/time. Obsolete versions are suppressed before sending. External delivery is bounded **at least once**: success before DB acknowledgement can cause a duplicate bearing the same notification ID; uncertain attempts are visible and capped. Overdue messages are currently one per entitlement/day, not a combined multi-service digest. Test only with synthetic recipients before enabling production notifications.
