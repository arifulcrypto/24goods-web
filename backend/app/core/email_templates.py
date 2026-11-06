# ruff: noqa: E501
"""Configurable email templates (``tenants.settings.email_templates``).

Four templates (verify_code, password_reset, order_paid, order_delivered), each
``{subject_i18n, body_i18n}`` per locale. Bodies are plain text or simple markdown: rendered
to a plain-text part (placeholders substituted verbatim) and an HTML part (markdown -> nh3
allow-list, substituted values markdown-escaped so a product title cannot inject links).
Placeholders: ``{site_name} {code} {minutes} {order_no} {order_url} {title} {amount}``;
unknown placeholders stay literal. Callers never pass card secrets (only these values).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.core.markdown import render

KINDS = ("verify_code", "password_reset", "order_paid", "order_delivered")
PLACEHOLDERS = ("site_name", "code", "minutes", "order_no", "order_url", "title", "amount")
LOCALES = ("en", "zh-CN")
MAX_SUBJECT = 150
MAX_BODY = 5000

DEFAULTS: dict[str, dict[str, dict[str, str]]] = {
    "verify_code": {
        "subject_i18n": {
            "en": "{site_name} verification code",
            "zh-CN": "{site_name} 验证码",
        },
        "body_i18n": {
            "en": "Your verification code is **{code}**. It is valid for {minutes} minutes.\n\n"
            "If you did not request it, ignore this email.",
            "zh-CN": "您的验证码是 **{code}**，{minutes} 分钟内有效。\n\n如非本人操作请忽略此邮件。",
        },
    },
    "password_reset": {
        "subject_i18n": {
            "en": "{site_name} password reset code",
            "zh-CN": "{site_name} 重置密码验证码",
        },
        "body_i18n": {
            "en": "Use code **{code}** to reset your password. It is valid for {minutes} minutes.\n\n"
            "If you did not request this, ignore this email; your password is unchanged.",
            "zh-CN": "请使用验证码 **{code}** 重置密码，{minutes} 分钟内有效。\n\n"
            "如非本人操作请忽略此邮件，您的密码不会改变。",
        },
    },
    "order_paid": {
        "subject_i18n": {
            "en": "{site_name}: payment received for order {order_no}",
            "zh-CN": "{site_name}：订单 {order_no} 已支付",
        },
        "body_i18n": {
            "en": "We received your payment of {amount} for **{title}** (order {order_no}).\n\n"
            "View your order: {order_url}",
            "zh-CN": "我们已收到订单 {order_no}（**{title}**）的付款 {amount}。\n\n查看订单：{order_url}",
        },
    },
    "order_delivered": {
        "subject_i18n": {
            "en": "{site_name}: order {order_no} delivered",
            "zh-CN": "{site_name}：订单 {order_no} 已发货",
        },
        "body_i18n": {
            "en": "Your order {order_no} (**{title}**) has been delivered.\n\n"
            "View it in your account: {order_url}",
            "zh-CN": "您的订单 {order_no}（**{title}**）已发货。\n\n请登录账户查看：{order_url}",
        },
    },
}

_PH = re.compile(r"\{(\w+)\}")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>#!|~&])")


def _clean_map(raw: Any, default: dict[str, str], max_len: int) -> dict[str, str]:
    out = copy.deepcopy(default)
    if isinstance(raw, dict):
        for k in LOCALES:
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()[:max_len]
    return out


def normalize(ts: dict[str, Any] | None) -> dict[str, dict[str, dict[str, str]]]:
    """Complete templates: stored values where set, built-in defaults elsewhere."""
    raw = (ts or {}).get("email_templates")
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, dict[str, str]]] = {}
    for kind in KINDS:
        r = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        out[kind] = {
            "subject_i18n": _clean_map(
                r.get("subject_i18n"), DEFAULTS[kind]["subject_i18n"], MAX_SUBJECT
            ),
            "body_i18n": _clean_map(r.get("body_i18n"), DEFAULTS[kind]["body_i18n"], MAX_BODY),
        }
    return out


def stored_after_patch(
    ts: dict[str, Any] | None, patch: dict[str, dict[str, dict[str, str]]]
) -> dict[str, Any]:
    """New ``email_templates`` value after a partial patch (kind -> field -> locale -> text).
    An empty string for a locale resets it to the built-in default."""
    cur = {k: {f: dict(m) for f, m in v.items()} for k, v in normalize(ts).items()}
    for kind, fields in patch.items():
        if kind not in KINDS:
            continue
        for fld in ("subject_i18n", "body_i18n"):
            for loc, text in (fields.get(fld) or {}).items():
                if loc not in LOCALES:
                    continue
                text = (text or "").strip()
                limit = MAX_SUBJECT if fld == "subject_i18n" else MAX_BODY
                cur[kind][fld][loc] = text[:limit] if text else DEFAULTS[kind][fld][loc]
    return cur


def _substitute(template: str, values: dict[str, str], *, escape: bool) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in PLACEHOLDERS or name not in values:
            return m.group(0)  # unknown placeholder: left literal
        v = values[name]
        return _MD_SPECIAL.sub(r"\\\1", v) if escape and name != "order_url" else v

    return _PH.sub(repl, template)


def render_template(
    ts: dict[str, Any] | None, kind: str, lang: str, values: dict[str, str]
) -> tuple[str, str, str]:
    """(subject, text body, html body) for ``kind`` in ``lang`` (falls back to en)."""
    tpl = normalize(ts)[kind]
    loc = lang if lang in LOCALES else "en"
    clean = {k: " ".join(str(v).split()) for k, v in values.items() if k != "order_url"}
    if "order_url" in values:
        clean["order_url"] = str(values["order_url"]).strip()
    subject = _substitute(tpl["subject_i18n"][loc], clean, escape=False)
    text = _substitute(tpl["body_i18n"][loc], clean, escape=False)
    html = render(_substitute(tpl["body_i18n"][loc], clean, escape=True))
    return subject, text, html


def site_name(ts: dict[str, Any] | None, lang: str, brand: str = "Kesiv") -> str:
    from app.core.settings_v5 import section

    names = section(ts, "general")["site_name_i18n"]
    default = "客思维" if lang == "zh-CN" else brand
    return names.get(lang) or names.get("en") or default


SAMPLE = {
    "code": "123456",
    "minutes": "10",
    "order_no": "GABCDEFGHIJKL",
    "order_url": "https://example.com/orders/GABCDEFGHIJKL",
    "title": "Sample product",
    "amount": "¥19.90",
}
