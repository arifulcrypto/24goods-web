"""Demo content for the seed: fixed pages, tutorial articles, announcement (EN + zh-CN)."""

from __future__ import annotations

from typing import Any

from app.devtools.help_content import ARTICLES as HELP_ARTICLES
from app.devtools.help_content import HELP_EN, HELP_ZH
from app.devtools.legal_content import (
    AFTER_SALES_EN,
    AFTER_SALES_ZH,
    PRIVACY_EN,
    PRIVACY_ZH,
    RULES_EN,
    RULES_ZH,
    TERMS_EN,
    TERMS_ZH,
)

PAGES: list[dict[str, Any]] = [
    {
        "slug": "help",
        "title": {"en": "Help guide", "zh-CN": "使用指南"},
        "body": {"en": HELP_EN, "zh-CN": HELP_ZH},
    },
    {
        "slug": "rules",
        "title": {"en": "Platform rules", "zh-CN": "平台规则"},
        "body": {"en": RULES_EN, "zh-CN": RULES_ZH},
    },
    {
        "slug": "after-sales",
        "title": {"en": "After-sales policy", "zh-CN": "售后政策"},
        "body": {"en": AFTER_SALES_EN, "zh-CN": AFTER_SALES_ZH},
    },
    {
        "slug": "terms",
        "title": {"en": "Terms of service", "zh-CN": "服务条款"},
        "body": {"en": TERMS_EN, "zh-CN": TERMS_ZH},
    },
    {
        "slug": "privacy",
        "title": {"en": "Privacy policy", "zh-CN": "隐私政策"},
        "body": {"en": PRIVACY_EN, "zh-CN": PRIVACY_ZH},
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

ARTICLES += HELP_ARTICLES

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
