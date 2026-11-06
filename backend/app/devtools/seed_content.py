"""Demo content for the seed: fixed pages, tutorial articles, announcement (EN + zh-CN)."""

from __future__ import annotations

from typing import Any

TEMPLATE_EN = "> **Template — have it reviewed by a lawyer before launch.**\n\n"
TEMPLATE_ZH = "> **模板 — 上线前请务必由律师审阅。**\n\n"

PAGES: list[dict[str, Any]] = [
    {
        "slug": "help",
        "title": {"en": "Help center", "zh-CN": "帮助中心"},
        "body": {
            "en": (
                "## How buying works\n\n"
                "1. Pick a product and tap **Buy**. You do not need to register: choose "
                "**Continue without registering** and we create an instant account for you.\n"
                "2. Pay with Alipay. Orders that are not paid within 30 minutes expire.\n"
                "3. **Instant** products are delivered automatically in seconds. **Manual** "
                "products are delivered by our support team, usually within 30 minutes.\n\n"
                "## Lost your login?\n\n"
                "Use **Order lookup** with your order number and the 10-character access code "
                "shown when you placed the order. Save it: it is the only way to recover an "
                "order without an account.\n\n"
                "## Need help?\n\n"
                "Open the chat from **Me → Contact online customer service**."
            ),
            "zh-CN": (
                "## 如何购买\n\n"
                "1. 选择商品并点击 **立即购买**。无需注册：选择 **无需注册，直接购买**，我们会为您创建"
                "临时账号。\n"
                "2. 使用支付宝付款。30 分钟内未付款的订单会自动失效。\n"
                "3. **自动发货** 商品付款后数秒内送达；**人工发货** 商品由客服处理，通常 30 分钟内完成。\n\n"
                "## 忘记登录信息？\n\n"
                "在 **订单查询** 中输入订单号和下单时显示的 10 位提取码即可找回订单。请务必保存提取码。\n\n"
                "## 需要帮助？\n\n"
                "在 **我的 → 联系在线客服** 中打开对话。"
            ),
        },
    },
    {
        "slug": "rules",
        "title": {"en": "Platform rules", "zh-CN": "平台规则"},
        "body": {
            "en": (
                "## Buyer rules\n\n"
                "- Read the product description, delivery format and after-sales terms before "
                "paying.\n"
                "- Digital goods are delivered once. Keep your codes private; we cannot recover "
                "codes you shared.\n"
                "- Use goods for lawful purposes only.\n\n"
                "## What we do\n\n"
                "- Stock is checked at purchase; we never sell the same code twice.\n"
                "- Prices come from our servers. An agent-negotiated price is shown on the order "
                "before you pay."
            ),
            "zh-CN": (
                "## 买家须知\n\n"
                "- 付款前请阅读商品描述、发货格式与售后条款。\n"
                "- 数字商品仅发货一次，请妥善保管，已泄露的卡密无法找回。\n"
                "- 请仅将商品用于合法用途。\n\n"
                "## 平台承诺\n\n"
                "- 购买时校验库存，同一卡密绝不重复出售。\n"
                "- 价格以服务器为准，客服议价后的价格会在付款前显示在订单上。"
            ),
        },
    },
    {
        "slug": "after-sales",
        "title": {"en": "After-sales policy", "zh-CN": "售后政策"},
        "body": {
            "en": (
                "## Window\n\n"
                "Each product shows its after-sales period (default **28 days**, some accounts "
                "**24 hours** from delivery). The deadline is printed on your order.\n\n"
                "## How to request\n\n"
                "Open the order and tap **Request after-sales**. Choose a reason, describe the "
                "problem and attach up to 4 screenshots. One request can be open per order.\n\n"
                "## Outcomes\n\n"
                "- **Replacement** of the code or account.\n"
                "- **Refund** to your original payment method.\n"
                "- **Rejection** with a reason, for example when a code was already used.\n\n"
                "Orders are completed automatically after delivery unless an after-sales request "
                "is open."
            ),
            "zh-CN": (
                "## 售后期限\n\n"
                "每个商品都标明售后期限（默认 **28 天**，部分账号为发货后 **24 小时**），截止时间显示在订单上。\n\n"
                "## 如何申请\n\n"
                "打开订单并点击 **申请售后**，选择原因、描述问题并上传最多 4 张截图。每个订单同一时间只能有"
                "一个进行中的申请。\n\n"
                "## 处理结果\n\n"
                "- **换货**：更换卡密或账号。\n"
                "- **退款**：退回原支付方式。\n"
                "- **驳回**：并说明原因，例如卡密已被使用。\n\n"
                "发货后订单会自动确认收货，售后申请处理期间自动确认会暂停。"
            ),
        },
    },
    {
        "slug": "terms",
        "title": {"en": "Terms of service", "zh-CN": "服务条款"},
        "body": {
            "en": TEMPLATE_EN
            + (
                "## 1. Service\n\nWe operate a marketplace for virtual goods and digital "
                "services.\n\n## 2. Accounts\n\nYou are responsible for activity under your "
                "account, including instant accounts.\n\n## 3. Payments and refunds\n\n"
                "Payments are processed by third-party providers. Refunds follow the "
                "after-sales policy.\n\n## 4. Prohibited use\n\nFraud, resale of stolen goods "
                "and abuse of the service are prohibited.\n\n## 5. Liability\n\nTo the extent "
                "permitted by law, our liability is limited to the price paid."
            ),
            "zh-CN": TEMPLATE_ZH
            + (
                "## 1. 服务内容\n\n本平台提供虚拟商品与数字服务交易。\n\n## 2. 账号\n\n您需对账号下的"
                "所有行为负责，包括临时账号。\n\n## 3. 支付与退款\n\n支付由第三方服务商处理，退款依据"
                "售后政策。\n\n## 4. 禁止行为\n\n禁止欺诈、销售来源不明商品及滥用服务。\n\n## 5. 责任"
                "限制\n\n在法律允许的范围内，我们的责任以您支付的价款为限。"
            ),
        },
    },
    {
        "slug": "privacy",
        "title": {"en": "Privacy policy", "zh-CN": "隐私政策"},
        "body": {
            "en": TEMPLATE_EN
            + (
                "## What we collect\n\nAccount identifiers (email or generated handle), order "
                "history, chat messages and technical logs needed to run the service.\n\n"
                "## What we do not do\n\nWe never store card codes in plaintext and we do not "
                "load third-party trackers or CDNs in the storefront.\n\n## Your choices\n\n"
                "You can edit your profile or delete your account in **Me**. Deleting removes "
                "personal data: your chat messages are redacted, files you uploaded are deleted and "
                "after-sales descriptions are cleared. Orders are kept in anonymised form for "
                "accounting. Security audit logs, which include IP addresses, are retained "
                "for fraud prevention and legal compliance."
            ),
            "zh-CN": TEMPLATE_ZH
            + (
                "## 我们收集的信息\n\n账号标识（邮箱或系统生成的账号名）、订单记录、聊天内容，以及运营所需"
                "的技术日志。\n\n## 我们不会做的事\n\n卡密绝不以明文存储，商城前端不加载任何第三方追踪或 "
                "CDN。\n\n## 您的选择\n\n您可在 **我的** 中修改资料或删除账号。删除后个人信息将被清除，订单"
                "以匿名形式保留用于记账。您的聊天内容将被清除，上传的文件将被删除，售后描述也会被清空。"
                "包含 IP 地址的安全审计日志将因防欺诈与合规需要而继续保留。"
            ),
        },
    },
]

ARTICLES: list[dict[str, Any]] = [
    {
        "slug": "redeem-steam-wallet-code",
        "category": "tutorial",
        "pinned": True,
        "title": {
            "en": "How to redeem a Steam Wallet code",
            "zh-CN": "如何兑换 Steam 钱包充值码",
        },
        "body": {
            "en": (
                "1. Open the order page and tap **Reveal** to see your code.\n"
                "2. Go to the [Steam redeem page](https://store.steampowered.com/account/"
                "redeemwalletcode) and sign in.\n"
                "3. Paste the code and confirm.\n\n"
                "> Each code works once and only in the matching region."
            ),
            "zh-CN": (
                "1. 打开订单页并点击 **查看卡密**。\n"
                "2. 访问 [Steam 兑换页面](https://store.steampowered.com/account/redeemwalletcode)"
                " 并登录。\n"
                "3. 粘贴卡密并确认。\n\n"
                "> 每个卡密仅可使用一次，且仅限对应地区。"
            ),
        },
    },
    {
        "slug": "log-in-to-a-purchased-account",
        "category": "tutorial",
        "pinned": False,
        "title": {
            "en": "Logging in to a purchased account safely",
            "zh-CN": "安全登录已购买的账号",
        },
        "body": {
            "en": (
                "Account products are delivered as `Account----Password----2FA----Notes`.\n\n"
                "- Log in **within 24 hours**; the after-sales period starts at delivery.\n"
                "- Use a clean browser profile and change the password right away.\n"
                "- Add your own recovery email before using the account.\n"
                "- Do not share the 2FA key."
            ),
            "zh-CN": (
                "账号类商品的发货格式为 `账号----密码----2FA----备注`。\n\n"
                "- 请在 **24 小时内** 登录，售后期限自发货起计算。\n"
                "- 使用干净的浏览器环境，并立即修改密码。\n"
                "- 使用前先绑定您自己的辅助邮箱。\n"
                "- 请勿泄露 2FA 密钥。"
            ),
        },
    },
    {
        "slug": "find-your-order-with-the-access-code",
        "category": "guide",
        "pinned": False,
        "title": {
            "en": "Find your order with the access code",
            "zh-CN": "使用提取码查询订单",
        },
        "body": {
            "en": (
                "Every order has a **10-character access code** shown on the order page and the "
                "payment-return page.\n\n"
                "1. Open **Order lookup**.\n"
                "2. Enter the order number and the access code.\n"
                "3. View your delivery, or reveal your codes.\n\n"
                "Lost it? Sign in and open the order to generate a new one."
            ),
            "zh-CN": (
                "每个订单都有一个 **10 位提取码**，显示在订单页和支付结果页。\n\n"
                "1. 打开 **订单查询**。\n"
                "2. 输入订单号和提取码。\n"
                "3. 查看发货内容或查看卡密。\n\n"
                "丢失了？登录后打开订单即可重新生成。"
            ),
        },
    },
]

ANNOUNCEMENT = {
    "version": 1,
    "enabled": True,
    "title_i18n": {"en": "Welcome to Kesiv", "zh-CN": "欢迎来到 Kesiv"},
    "body_i18n": {
        "en": (
            "This is a **demo** with mock data: payments are simulated and nothing is shipped.\n\n"
            "Please read the Help center before buying."
        ),
        "zh-CN": (
            "这是使用 **模拟数据** 的演示站：支付为模拟，不会真实发货。\n\n购买前请先阅读帮助中心。"
        ),
    },
}

# Production seed: disabled by default; the admin can enable/edit it in the console.
ANNOUNCEMENT_PROD = {
    "version": 1,
    "enabled": False,
    "title_i18n": {"en": "Welcome to Kesiv", "zh-CN": "欢迎来到 Kesiv"},
    "body_i18n": {
        "en": "Please read the Help center before buying.",
        "zh-CN": "购买前请先阅读帮助中心。",
    },
}

SUPPORT_HOURS = {"en": "Daily 09:00–23:00 (UTC+8)", "zh-CN": "每天 09:00–23:00（UTC+8）"}
