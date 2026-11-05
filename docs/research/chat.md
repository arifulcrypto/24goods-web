# Research: Chat, voice and negotiation components

> Researched 2026-11-05 by a research sub-agent. **[V]** = verified this session (GitHub API, repo source, official docs). **[I]** = inferred; re-verify before relying on it.

## TL;DR

Build the chat domain **yourself in FastAPI**. Negotiation (quote → order → payment from a chat card) is core business logic; help-desk products treat it as foreign data.

- **Realtime transport:** [Centrifugo](https://github.com/centrifugal/centrifugo) OSS (Apache-2.0), Postgres as source of truth.
- **Voice calls:** [LiveKit](https://github.com/livekit/livekit) OSS (Apache-2.0) in Japan with built-in TURN on TLS/443; minimal alternative = P2P WebRTC + [coturn](https://github.com/coturn/coturn).
- **Voice messages:** browser `MediaRecorder` → presigned upload → server transcode.
- **Chatwoot:** not for buyer↔seller. Optional later as an internal back office for platform staff (disputes).

## 1. Chatwoot — https://github.com/chatwoot/chatwoot · https://www.chatwoot.com · https://developers.chatwoot.com

- 38.9k★, pushed 2026-11-05, v4.19.1 (2026-10-24) **[V]**.
- **License [V]:** MIT for everything outside `enterprise/`; `enterprise/LICENSE` requires a paid Enterprise licence for production use. Self-hosted plans ([pricing](https://www.chatwoot.com/pricing/self-hosted-plans)): Community $0 · Premium $19/agent/mo (Captain AI, **voice calls**, branding, roles) · Enterprise $99/agent/mo.
- **Footprint [V]** ([requirements](https://developers.chatwoot.com/self-hosted/deployment/requirements)): ≥4 GB RAM / 4 cores (~10k conv/day); Rails + Sidekiq + PG + Redis 7 — a second monolith next to yours.
- **APIs [V]:** Application / Client / Platform APIs; webhooks signed HMAC-SHA256 (`X-Chatwoot-Signature`). Agent availability enum `online/offline/busy`.
- **Structured messages [V]:** `content_type` ∈ `text, input_select, cards, form, article, input_csat, voice_call…`. A "quote card" can only be faked as a `cards` message with a "Pay" link to your checkout — no custom renderer, no expiry countdown, no live price state ([interactive messages](https://www.chatwoot.com/hc/user-guide/articles/1677689344-how-to-use-interactive-messages)).
- **Voice messages [V]:** `AudioRecorder.vue` exists only in the agent dashboard; **customers cannot record in the widget** (open request [#9183](https://github.com/chatwoot/chatwoot/issues/9183)).
- **Voice calls [V]:** `enterprise/app/models/call.rb` → providers `twilio | whatsapp`, default STUN `stun.l.google.com` (blocked in CN). Calls are paid, PSTN/WhatsApp — **no buyer↔seller browser WebRTC**.
- **C2C fit [I]:** possible via one Chatwoot account per merchant (Platform API) but every seller needs a second login/UI, identities and quotes must be synced via webhooks → doubled attack surface and PII stores. Mobile app relies on FCM (unusable in CN).

## 2. Crisp — https://crisp.chat

- SaaS, Free (2 seats) / $45 / $95 / $295 per workspace per month **[V]** ([pricing](https://crisp.chat/en/pricing/)).
- Crisp's own help centre: *"Russia and China may block some of our messaging servers"* and suggests a VPN **[V]** ([article](https://help.crisp.chat/en/article/i-dont-receive-messages-in-real-time-on-my-crisp-dashboard-pcoi37)).
- Third-party data custody, no per-seller isolation, no quote/order binding → **rejected**.

## 3. Comparison

| Option | License [V] | ★ / last push | Stack / RAM | C2C + quote cards | Voice msg | Calls | Verdict |
|---|---|---|---|---|---|---|---|
| Chatwoot | MIT core + proprietary `enterprise/` | 38.9k / 2026-11-05 | Rails+Sidekiq+PG+Redis, ≥4 GB | Poor (link-out cards) | Agent only | Twilio/WhatsApp, paid | Optional staff desk |
| Crisp | Proprietary SaaS | — | — | No | — | Plan-dependent | Reject (CN blocking) |
| **Centrifugo** | Apache-2.0 (PRO commercial) | 10.9k / 2026-11-04 | Go binary + Redis, light | Transport only → yes | n/a | n/a | **Recommended transport** |
| OpenIM | Server Apache-2.0, **SDK core AGPL-3.0** | 16.8k / 2026-11-05 | Go microservices + Mongo + Kafka + MinIO (heavy) | Customise | Yes | Via LiveKit | AGPL SDK + heavy ops → no |
| Tinode | Server GPL-3.0, clients Apache-2.0 | 13.6k / 2026-10-25 | Go + PG/MySQL | Fork needed | Yes | Yes | GPL + foreign data model |
| Rocket.Chat | MIT + proprietary `ee/` | 46.4k / 2026-11-05 | Node + Mongo, 2–4 GB | Team-chat model | Yes | Paid | Overkill |
| Matrix/Synapse | AGPL-3.0 | 4.8k / 2026-11-04 | Python + PG | Poor | Yes | Element Call | No |
| Zulip | Apache-2.0 | 26.2k / 2026-11-05 | ≥2 GB | Threaded team chat | Partial | No | No |
| Papercups | MIT | 6.1k / 2024-02-15 | Elixir | — | — | — | Dead (maintenance mode) |
| **LiveKit** | Apache-2.0 | 22.1k / 2026-11-04 | Go SFU, built-in TURN | — | — | Yes | **Recommended for calls** |
| coturn | BSD-3-Clause | 14.5k / 2026-11-03 | C, tiny | — | — | TURN | Fallback / P2P relay |

## 4. Recommended architecture

- **Message service (FastAPI, PG = source of truth):** clients `POST /conversations/{id}/messages` with an idempotency key; server validates → moderates → persists → publishes to Centrifugo via server API. This is Centrifugo's documented model ("backend publishes, clients subscribe"; Centrifugo "is not the source of truth" — [design](https://centrifugal.dev/docs/getting-started/design)) **[V]**.
- **Centrifugo OSS:** WebSocket + SSE/HTTP-streaming fallbacks (helps on lossy CN→JP links), Redis engine, presence (online status), history + recovery. FastAPI issues JWT channel tokens; personal channel `user:{id}` ⇒ a seller only receives their own conversations. PRO not needed.
- **Alternative:** native FastAPI WebSockets + Redis pub/sub + Redis Streams for gap recovery. Note `encode/broadcaster` is **archived** [V]. Works, but you re-implement ping/pong, reconnect, recovery, presence and fallbacks.
- **Calls:** LiveKit in Tokyo, one room per call, tokens only for conversation participants. Ports: 443/TCP (HTTPS + TURN/TLS), 3478/UDP, 7881/TCP, 50000–60000/UDP ([VM guide](https://docs.livekit.io/home/self-hosting/vm/)). Cross-border UDP from CN is often throttled → force TURN/TLS:443 fallback. Never use Google STUN.
- **Voice messages:** `audio/webm;codecs=opus` (iOS Safari → `audio/mp4` AAC; feature-detect `isTypeSupported`), ≤60 s / ~1 MB, presigned PUT, transcode to Opus + AAC, store duration + waveform.
- **China rules [I]:** serve every JS/font/avatar from your own domain (no Google Fonts, reCAPTCHA, Firebase, Gravatar); FCM/Web Push unusable on most CN Android → in-app badges + email/Telegram notifications.

## 5. Data model sketch

- `conversations(id, kind[buyer_seller|support], buyer_id, seller_id, product_id?, status, last_message_id, created_at)` — UNIQUE(buyer_id, seller_id, product_id) so "Chat with seller" reopens the same thread.
- `conversation_participants(conversation_id, user_id, role, last_read_message_id, muted)` — read receipts via `last_read_message_id`.
- `messages(id, conversation_id, sender_id, type[text|image|voice|product_card|quote_card|order_card|system], body_enc, payload JSONB snapshot, attachment_id, client_msg_id UNIQUE per sender, moderation_state, created_at, deleted_at)`.
- `attachments(id, owner_id, kind, object_key, mime, bytes, sha256, duration_ms, w, h, scan_state)` — signed URLs only.
- `quotes(id, conversation_id, seller_id, buyer_id, product_id, amount_minor, currency, qty, terms_json, status, expires_at, supersedes_quote_id, signature HMAC, version)`.
- `orders(..., quote_id UNIQUE, price_minor snapshot, status)` + `order_price_changes(old, new, by, reason, at)` audit log.

**Quote state machine:** `offered → accepted | rejected | withdrawn | expired | superseded`. Seller offers/withdraws; buyer accepts. Accept = one transaction: `SELECT … FOR UPDATE` quote, check status/expiry/signature, create order `pending_payment`, post `order_card` system message.

**Order:** `pending_payment → paid → delivering → delivered → completed`, exits `cancelled | refund_requested | refunded | disputed`. Seller may change price only in `pending_payment` (bumps `version`, audit row, system message). Pay button always requests a fresh server-side payment intent for the current version. Only verified payment notifies move to `paid`.

**Anti-abuse:** Redis token buckets per user/conversation/IP; contact & off-platform-payment detection (full-width digits, Chinese numerals, "V信/vx/wx/QQ/TG/@", URLs, 支付宝/微信转账/USDT) → warn + mask + flag; images re-encoded, EXIF stripped, ClamAV, **QR-code detection** (#1 bypass), OCR through same filters; block/report on every thread.

**Encryption at rest:** encrypted volumes and backups + app-level envelope encryption of `messages.body` and attachments. **No E2EE** — the platform must read chats to arbitrate disputes. Define retention (e.g. N months after order close, longer under dispute).
