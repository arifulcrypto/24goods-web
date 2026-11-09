"""Help center content (EN + zh-CN): the help page and general help articles.

Kept general on purpose (payment methods, timeouts and networks change per site and over time):
where a number matters, the text points at what the checkout / order page shows.
"""

from __future__ import annotations

from typing import Any

from app.devtools.legal_content import SUPPORT_EMAIL, _flow

HELP_EN = f"""## Getting started

You can browse without an account. To buy, sign in with your email, use a supported third-party
sign-in, or choose **Continue without registering** at checkout and an account is created for you.

## Buying

1. Open a product and check its region, delivery time and after-sales period.
2. Tap **Buy** (or add it to the cart) and fill in any details the product asks for.
3. Choose a payment method and pay within the time shown on the payment page.

## Payment

Available methods are shown at checkout. Pay the exact amount shown; for digital assets, use the
network shown. Paid but the order has not updated after a few minutes? Contact customer service
with the order number and your payment reference.

## Delivery

**Instant** products are delivered automatically, usually within seconds of payment. **Manual**
products are delivered by the seller within the time shown on the product. Your delivery appears on
the order page and you are notified when it arrives.

## Find an order without signing in

Use **Order lookup** with the order number and the access code shown after you placed the order.
Save them: they are the easiest way back to an order you placed without an account.

## Problems with an order

Open the order and tap **Request after-sales** within the after-sales period. See the
[After-sales policy](/a/after-sales) for what is covered.

## Contact us

Chat with us from **Me → Contact online customer service**, or email {SUPPORT_EMAIL}.
"""

HELP_ZH = f"""## 新手入门

无需账号即可浏览。购买时可使用邮箱登录、使用支持的第三方账号登录，或在结算时选择 **无需注册，继续购买**，系统会
为您自动创建账号。

## 如何购买

1. 打开商品页，确认地区、发货时间及售后期限。
2. 点击 **立即购买**（或加入购物车），并填写商品要求的信息。
3. 选择支付方式，并在支付页显示的时限内完成付款。

## 付款

可用的支付方式以结算页显示为准。请支付页面显示的准确金额；使用数字资产付款时，请使用页面显示的网络。付款后几分钟
订单仍未更新？请联系客服并提供订单号及付款凭证。

## 发货

**自动发货** 商品付款后通常数秒内自动送达；**人工发货** 商品由卖家在商品页注明的时间内发货。发货内容显示在订单页，
送达时您会收到通知。

## 未登录查询订单

在 **卡密查询** 中输入订单号和下单时显示的查询码即可。请妥善保存，未注册下单时这是找回订单最便捷的方式。

## 订单遇到问题

在售后期限内打开订单并点击 **申请售后**。保障范围请参阅[售后政策](/a/after-sales)。

## 联系我们

在 **我的 → 联系在线客服** 中与我们对话，或发送邮件至 {SUPPORT_EMAIL}。
"""

# New general articles. Seeded once (by slug) and never overwritten, like the rest of the content.
ARTICLES: list[dict[str, Any]] = [
    {
        "slug": "payment-methods-and-issues",
        "category": "faq",
        "pinned": True,
        "title": {
            "en": "Payment methods and common payment issues",
            "zh-CN": "支付方式及常见支付问题",
        },
        "body": {
            "en": """**Which methods can I use?** The methods available for your order are listed at
checkout. Some carry a small surcharge, which is shown before you pay.

**How long do I have to pay?** The payment page shows a countdown. Orders that are not paid in
time expire automatically; just place a new order.

**Paying with digital assets:**

- Send the exact amount shown, on the network shown, to the address shown for this order.
- Network fees are paid by the sender. Short or late payments may not be credited.
- Most payments confirm within minutes, depending on the network.

**I paid but the order still says unpaid.** Wait a few minutes and refresh. If it has not changed,
contact customer service with the order number and your payment reference (for example the
transaction ID). Do not pay a second time.

**Can I get a refund to a different method?** No. Refunds always go back to the original payment
method.""",
            "zh-CN": """**可以使用哪些支付方式？** 可用的支付方式在结算页列出，部分方式会收取少量手续费，付款前会显示。

**付款时限是多久？** 支付页会显示倒计时。超时未付款的订单会自动失效，重新下单即可。

**使用数字资产付款：**

- 请按本订单显示的金额、网络和地址准确转账。
- 网络手续费由付款方承担。少付或逾期付款可能无法入账。
- 视网络情况，大多数付款几分钟内即可确认。

**已付款但订单仍显示未付款。** 请稍等几分钟后刷新。如仍未更新，请联系客服并提供订单号及付款凭证（例如交易 ID），
请勿重复付款。

**可以退款到其他支付方式吗？** 不可以，退款一律退回原支付方式。""",
        },
    },
    {
        "slug": "delivery-instant-and-manual",
        "category": "faq",
        "pinned": False,
        "title": {"en": "Delivery: instant and manual", "zh-CN": "发货说明：自动发货与人工发货"},
        "body": {
            "en": """- **Instant delivery:** the item is delivered automatically right after payment
is confirmed, usually within seconds.
- **Manual delivery:** the seller prepares and delivers the item within the time shown on the
product page. Rush requests do not change the queue.

**Where is my delivery?** On the order page (**Me → My orders**). Codes may be hidden until you tap
**Reveal codes**. You are also notified when a manual order is delivered.

**The delivery is late.** If the stated time has passed, open the order and request after-sales
with the reason *Not received*.

**Keep it private.** Anyone who sees a code or account details can use them. We cannot recover
items you have shared.""",
            "zh-CN": """- **自动发货：** 付款确认后系统立即自动发货，通常数秒内送达。
- **人工发货：** 卖家在商品页注明的时间内处理并发货，催单不会改变处理顺序。

**在哪里查看发货内容？** 在订单页（**我的 → 我的订单**）。卡密可能需要点击 **查看卡密** 后才显示。人工发货订单送达时
您也会收到通知。

**发货超时怎么办？** 如已超过注明时间，请打开订单申请售后，原因选择 *未收到商品*。

**请妥善保管。** 任何看到卡密或账号信息的人都可以使用。已泄露的商品无法找回。""",
        },
    },
    {
        "slug": "how-to-request-after-sales",
        "category": "tutorial",
        "pinned": False,
        "title": {"en": "How to request after-sales", "zh-CN": "如何申请售后"},
        "body": {
            "en": """1. Open **Me → My orders** and select the order.
2. Check the after-sales deadline on the order, then tap **Request after-sales**.
3. Pick the reason that fits best and describe what happened.
4. Attach clear screenshots or a screen recording showing the error message, with the time visible
where possible.
5. Submit, then watch for replies in the order chat and answer follow-up questions.

**Tips for a quick resolution**

- Do not change or reuse the item while the request is open.
- One request per order can be open at a time; add new information to the same request.
- Read the [After-sales policy](/a/after-sales) for what is covered and possible outcomes.""",
            "zh-CN": """1. 打开 **我的 → 我的订单**，选择对应订单。
2. 确认订单上的售后截止时间，然后点击 **申请售后**。
3. 选择最符合的原因并描述问题经过。
4. 上传清晰的截图或录屏，显示错误提示，尽量包含时间。
5. 提交后留意订单对话中的回复，并及时回答客服的询问。

**快速处理小贴士**

- 申请处理期间请勿修改或继续使用该商品。
- 每个订单同一时间只能有一个进行中的申请，新的信息请补充在同一申请中。
- 保障范围及处理结果请参阅[售后政策](/a/after-sales)。""",
        },
    },
    {
        "slug": "keep-your-account-secure",
        "category": "guide",
        "pinned": False,
        "title": {"en": "Keep your account secure", "zh-CN": "保护您的账号安全"},
        "body": {
            "en": """**Your Kesiv account**

- Use a unique password, or sign in with a third-party account you control.
- Bought without registering? Set an email and password in **Me → Settings** so you can sign in
again on another device.
- Forgot your password? Use **Forgot password?** on the sign-in page.
- We will never ask for your password or verification codes in chat.

**Accounts you buy**

- Sign in and check the account as soon as it is delivered, within the after-sales period.
- Change the password and add your own recovery email or phone as instructed in the delivery.
- Keep two-factor keys private and do not share the account.""",
            "zh-CN": """**您的 Kesiv 账号**

- 使用独立的密码，或使用您本人控制的第三方账号登录。
- 未注册直接购买？请在 **我的 → 设置** 中设置邮箱和密码，以便在其他设备上再次登录。
- 忘记密码？在登录页点击 **忘记密码**。
- 我们绝不会在对话中索要您的密码或验证码。

**您购买的账号**

- 收到后请在售后期限内尽快登录并检查。
- 按发货说明修改密码，并绑定您自己的辅助邮箱或手机号。
- 妥善保管两步验证密钥，请勿共享账号。""",
        },
    },
    {
        "slug": "subscriptions-and-renewals",
        "category": "guide",
        "pinned": False,
        "title": {"en": "Subscriptions and renewals", "zh-CN": "订阅与续费"},
        "body": {
            "en": """Some products are subscriptions or time-based services, usually billed per
calendar month.

- **Service period:** starts when the order is delivered. The period and its end date are shown on
the order.
- **Renewing:** buy the same product again before the end date; the new period is added after the
current one, so you do not lose any days. Contact customer service if you want it applied to a
different account.
- **Reminders:** we may remind you before the period ends.
- **Problems during the period:** request after-sales on the order; remedies include restoring the
service, extending the period or a refund for the unused part.""",
            "zh-CN": """部分商品为订阅或按时长计费的服务，通常按自然月计费。

- **服务期：** 自订单发货时起算，服务期及到期时间显示在订单上。
- **续费：** 在到期前再次购买同一商品，新的服务期会接在当前服务期之后，不会损失天数。如需用于其他账号，
请联系客服。
- **提醒：** 到期前我们可能会提醒您。
- **服务期内出现问题：** 请在订单上申请售后，处理方式包括恢复服务、延长服务期或退还未使用部分的费用。""",
        },
    },
    {
        "slug": "chat-and-custom-quotes",
        "category": "guide",
        "pinned": False,
        "title": {"en": "Chat with us and get a custom price", "zh-CN": "在线咨询与议价"},
        "body": {
            "en": """- Tap **Chat** on a product, or open **Me → Contact online customer service**, to talk
to us. You can send text, images and voice messages.
- For bulk or custom requests, ask for a quote in the chat. An agreed price is sent to you as an
order you can pay; only the price on the order counts.
- Keep payment and communication on the platform. Requests to pay elsewhere are not from us.
- Replies are fastest during the support hours shown in the chat.""",
            "zh-CN": """- 在商品页点击 **聊一聊**，或打开 **我的 → 联系在线客服** 与我们对话，可发送文字、图片和语音。
- 批量或定制需求可在对话中询价。议定的价格会以订单形式发送给您付款，价格以订单显示为准。
- 请在平台内完成付款和沟通，任何要求站外付款的消息都不是我们发出的。
- 在对话中显示的客服时间内回复最快。""",
        },
    },
]

HELP_EN, HELP_ZH = _flow(HELP_EN), _flow(HELP_ZH)
for _a in ARTICLES:
    _a["body"] = {k: _flow(v).rstrip("\n") for k, v in _a["body"].items()}
