# V6 implementation review record

The first read-only security/payment/mobile source review of `0335f87` **failed**. It did not run an exploit, use merchant credentials, make a real payment or certify a deployed host. The following concrete findings were addressed in the follow-up patch; final-review/CI evidence belongs to the final revision, not the initial failed snapshot.

| Finding | Correction and regression |
|---|---|
| Unauthenticated native numeric canonicalization can expand extreme exponents | Bound numeric token length/digits/magnitude/exponent before formatting; reject nonfinite values and oversized integers. Tests use small synthetic boundary violations. |
| UUID-prefixed upstream trade IDs were silently truncated | Canonical collision-resistant 64-character identity is used consistently; original IDs remain bounded trace metadata. Tests use long distinct IDs with identical prefixes and repeat callbacks past the Redis guard. |
| Cancellation followed the current channel/latest USDT attempt | Query all durable original EPay and USDT attempts independently of current configuration; missing/unknown evidence blocks cancellation. Legacy drain paths remain separate. |
| Unknown native create result could bypass receiver checks | Record verified money but hold when trustworthy original trade/address/amount evidence is absent; retain original encrypted credentials for uncertain requests. |
| Recovery returned at the first paid original | Process all originals, preserve committed outcomes and escalate additional paid trades; background recovery emits existing order/alert events. |
| Malformed/auth query errors were treated as “not found” | Accept only explicit recognized no-such-order responses; unknown codes/statuses and order-binding mismatches are failures. |
| Legacy response caps applied after buffering | All payment transports use capped streaming and request identity encoding; compressed bodies are rejected before decoding. |
| Rejected login events amplified DB writes | Sample per IP/minute plus a tenant-wide eight-row minute budget; count rejected attempts cheaply in Redis. Successful-login events remain explicit. |
| Admin read calls shared a mutation lock/toast | Independent read states; overview and events load concurrently without “Saved” messages. |
| Absolute asset URLs bypassed small previews | Same-origin URL parsing and query construction; regression follows the actual safeAssetUrl → preview pipeline. |
| New recovery errors were unknown to the client | Register codes and paired EN/zh-CN guidance, including “do not pay again” for uncertain payment. |
| Pending-service edits sent an empty expiry / mutated rows early | Clone editable state; omit absent/unchanged dates; reload only after successful mutation. |
| Staleness used a nonreactive clock | Reactive clock plus failed-refresh state. |
| Quote action overwrote an unsent draft | Preserve the existing draft after the quoted excerpt. |
| Security modal lacked focus lifecycle | Focus capture/trap/restore, Escape cancellation and inert background while solving. |
| Expired entry clearance did not reopen the gate | Central handling of server-required clearance plus visibility/periodic checks; payment/recovery routes stay accessible. |
| Pinned content overflowed / raw status enums leaked into translated UI | Wrap long tokens and translate operational/service display states. |

Dedicated tests cover payment/query/numeric/stream/receiver/history and denied-log boundaries, plus parallel read state and actual thumbnail URL behavior. Existing channel-switch and account-deletion regressions were run because integration exposed compatibility failures; the fixes preserve private-input redaction and legitimate pre-cutover payment draining.

Physical Safari/Chrome/embedded-wallet/assistive-technology validation is still an explicit release check. Source inspection, unit tests and CI do not demonstrate every device's clipboard, long-press, Turnstile or QR album-scanning behavior. No deliberately vulnerable honeypot, production traffic test or live provider/key rotation was performed.

## Second review checkpoint — f414c59

**FAIL, stopped at owner request.** Two Medium issues remain: unconfigured legacy EPay fall-through after unpaid USDT recovery, and committed notifications/duplicate alerts dropped when a different original remains unresolved. Preserve outcomes independently from unresolved status while keeping cancellation fail-closed. One minor event-family localization item remains. Full reproduction/next steps and final CI caveats are in [HANDOFF-v6.md](../HANDOFF-v6.md). No browser/device validation was performed.
