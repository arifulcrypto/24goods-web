"""Language resolution: ``?lang=`` -> ``Accept-Language`` -> ``en``."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.requests import Request

SUPPORTED = ("en", "zh-CN")
DEFAULT = "en"


def normalize(tag: str) -> str | None:
    t = tag.strip().lower().replace("_", "-")
    if not t:
        return None
    if t == "en" or t.startswith("en-"):
        return "en"
    if t == "zh" or t.startswith(("zh-cn", "zh-hans", "zh-sg")):
        return "zh-CN"
    if t.startswith("zh-"):
        return "zh-CN"  # only one Chinese locale in MVP
    return None


def parse_accept_language(header: str) -> str | None:
    candidates: list[tuple[float, int, str]] = []
    for idx, part in enumerate(header.split(",")[:20]):
        pieces = part.split(";")
        q = 1.0
        for p in pieces[1:]:
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        lang = normalize(pieces[0])
        if lang and q > 0:
            candidates.append((-q, idx, lang))
    candidates.sort()
    return candidates[0][2] if candidates else None


def resolve_lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q:
        lang = normalize(q)
        if lang:
            return lang
    header = request.headers.get("accept-language")
    if header:
        lang = parse_accept_language(header)
        if lang:
            return lang
    return DEFAULT


def pick(values: Mapping[str, str] | None, lang: str) -> str:
    """Resolve a localized jsonb dict, falling back to English."""
    if not values:
        return ""
    return values.get(lang) or values.get(DEFAULT) or next(iter(values.values()), "")
