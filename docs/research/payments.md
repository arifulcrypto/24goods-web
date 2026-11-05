# Research: Payment channels & callback security

> Researched 2026-11-05 by a research sub-agent that read gateway source code (not executed). **[V]** = verified in source / official docs. **[I]** = inferred.

## Summary

| Channel | Type | Signing | License / SaaS | Key risks | Recommendation |
|---|---|---|---|---|---|
| **[BEpusdt](https://github.com/v03413/bepusdt)** | Self-hosted non-custodial crypto gateway (Go) | MD5(sorted `k=v&…` + token) [V] | GPL-3.0 | Token leak = forged callbacks; RPC reliance; **confirmations default 0**; PRC legal exposure | **Primary crypto rail**, on the private network |
| UPay ([repo](https://github.com/UPay-USDT/USDT-Payment-Gateway), [upay.ink](https://upay.ink)) | PHP/ThinkPHP + hosted SaaS | UPPER(MD5(ksorted + `&appsecret=`)) [V] | GPL-3.0, single commit 2024-10 | Abandoned, PHP 7.4 EOL, TRC20/ERC20 only, SaaS fee | **Avoid** — BEpusdt covers it |
| [ZPay](https://z-pay.cn) ([docs](https://z-pay.cn/doc.html)) | Hosted 易支付-compatible aggregator (Alipay/WeChat) | MD5 only [V] | SaaS, ¥88–288 setup, ~1% + 0.6%, D+1 | Third-party custody, freezes/bans, merchant key in query URL | Optional CNY rail behind kill-switch, low limits |
| 彩虹易支付 / EPay protocol ([mirror](https://github.com/maajiko/Epay)) | Protocol used by many 四方 aggregators | V1 MD5; **V2 SHA256WithRSA + timestamp** [V] | Unclear licensing | Unchecked sign/amount, replay, double delivery | Implement as an **adapter** only |
| [Cryptomus](https://doc.cryptomus.com) / NOWPayments / Plisio | Hosted crypto | Cryptomus md5(base64(json)+key), IP 91.227.144.54 [V]; NOWPayments HMAC-SHA512 [I] | SaaS, KYB | Custody, freezes | Backup for international crypto |
| Stripe / PayPal | Cards/wallets | HMAC webhooks | SaaS | Stripe restricts in-game items / gift cards / virtual credits [V]; chargebacks | Probably not viable for this category |

## BEpusdt details [V]

- v1.24.3 (2026-10-21). Chains: Tron (USDT/USDC/TRX), Ethereum, Polygon, BSC, Solana, TON, Aptos, X-Layer, Arbitrum, Base, Plasma ([trade types](https://github.com/v03413/BEpusdt/blob/main/docs/trade-type.md)). Epusdt-API compatible; also accepts 易支付 `submit.php`.
- `POST /api/v1/order/create-transaction` (`order_id, amount, fiat, trade_type, notify_url, redirect_url, timeout, signature`) → `trade_id, actual_amount, token(address), payment_url` ([API](https://github.com/v03413/BEpusdt/blob/main/docs/api/api.md)).
- ⚠️ **`amount=0` enables "address-exclusive mode" — any received amount triggers the callback.** Validate amount > 0 before calling.
- Callback: POST JSON `trade_id, order_id, amount, actual_amount, token, block_transaction_id, status(1 waiting/2 paid/3 expired), signature`; retries 10× with backoff. **Code only checks HTTP 200** (docs say `ok`) → return non-200 on failure. Ignore status 1/3 for fulfilment.
- Signature (`app/utils/utils.go EpusdtSign`): drop `signature` + empty, sort, `k=v&…`, `md5(str+token)`. Values via Go `%v` after float64 round-trip → pin Python with test vectors (doc vector `1cd4b52df5587cfb1968b0c0c6e156cd`).
- Re-query: `POST /api/v1/pay/info {trade_id}` is **unauthenticated** → only reachable over the private network.
- Match mode: use `classic` (exact). Set `block_offset_confirm` > 0 (≈19 Tron, 12+ ETH, ~15 BSC/Polygon [I]).
- Ops: watch-only addresses only (BEpusdt never needs private keys); admin :8080 firewalled; screen for tainted USDT ("黑U"); pin release + image digest.
- GPL-3.0 as a separate unmodified process over HTTP = aggregate → no obligation on your code ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)) [I].

## EPay protocol [V]

- V1: ksort, skip empty / `sign` / `sign_type`, `md5(str+key)`; GET notify `pid, trade_no, out_trade_no, type, name, money, trade_status[, param], sign, sign_type`. **No timestamp → replayable.**
- V2: `/api/pay/create`, `/api/pay/query`; merchant signs SHA256WithRSA; `timestamp` ±300 s; callbacks signed with platform private key.
- `return_url` receives the same signed query → **display only, never fulfil from it.**
- Merchant-side failure modes: no sign check / attacker-chosen `sign_type` (**pin per channel; never RSA→MD5 fallback**); no amount check (¥0.01 order trick); no `pid` check (another merchant's validly-signed callback); no `trade_status` check; replays; concurrent duplicate callbacks → double delivery; raw vs URL-decoded signing mismatch.

## Hardened notify pipeline (FastAPI)

1. One adapter per channel; verify over raw values with `hmac.compare_digest`; pin `sign_type`.
2. Callback = hint. **Re-query the gateway server-to-server** and act only on that answer.
3. Compare merchant id, Decimal amount, currency/trade_type, receiving address (crypto), status, confirmations against **your own** order record.
4. Idempotency: `UNIQUE(channel, gateway_trade_no)`, `SELECT … FOR UPDATE`, state machine where only `PENDING→PAID` fulfils.
5. Replay window: timestamp ±300 s where available, else Redis `SET NX EX` on callback hash. IP allowlist where published; private network for BEpusdt.
6. **Outbox**: insert `outbox(order.paid)` in the same transaction; worker delivers + posts **double-entry ledger** (buyer clearing → merchant payable → agent commission → platform revenue; sum = 0).
7. Mismatched / late / over-payments → `MANUAL_REVIEW`, never auto-fulfil.

```python
class PaymentAdapter(Protocol):
    name: str
    async def create(self, order: Order) -> CheckoutInfo: ...
    def parse_and_verify(self, params: dict[str, str], raw_body: bytes) -> VerifiedNotice: ...  # raises BadSignature
    async def query(self, gateway_trade_no: str, merchant_ref: str) -> VerifiedNotice: ...  # S2S truth
    def ack(self, ok: bool) -> Response: ...  # BEpusdt: 200 / 503; EPay: "success" / "fail"

@router.api_route("/pay/notify/{channel}", methods=["GET", "POST"])
async def notify(channel: str, request: Request):
    ad = ADAPTERS.get(channel) or abort(404)
    raw = await request.body()
    try:
        n = ad.parse_and_verify(merged_params(request), raw)
    except BadSignature:
        log_security_event(channel, request); return ad.ack(False)
    if not await redis.set(f"cb:{channel}:{sha256(raw)}", 1, nx=True, ex=86400):
        return ad.ack(True)                       # exact replay
    if not n.paid: return ad.ack(True)
    truth = await ad.query(n.gateway_trade_no, n.merchant_ref)
    async with db.begin():
        o = await db.scalar(select(Order).where(Order.id == truth.merchant_ref).with_for_update())
        if o is None or not truth.paid: return ad.ack(False)
        if (o.channel, o.currency, o.expected_amount) != (channel, truth.currency, truth.amount):
            o.state = "MANUAL_REVIEW"; return ad.ack(True)
        if o.state == "PAID": return ad.ack(True)
        if o.state != "PENDING": o.state = "MANUAL_REVIEW"; return ad.ack(True)
        db.add(Payment(channel=channel, gateway_trade_no=truth.gateway_trade_no, order_id=o.id, amount=truth.amount))
        o.state = "PAID"
        db.add(Outbox(topic="order.paid", key=o.id))
    return ad.ack(True)
```

## Legal

PRC 银发〔2021〕237号 classifies virtual-currency business as illegal financial activity; providers of payment/technical support can be liable ([gov.cn](https://www.gov.cn/zhengce/zhengceku/2021-10/08/content_5641404.htm)) [V]. 四方支付 operators have been convicted of 非法经营罪 ([The Paper, 2026-06](https://www.thepaper.cn/newsDetail_forward_33381335)). **Get legal review before marketing USDT or 四方 CNY payment to mainland users.**
