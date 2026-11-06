from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LOCALES = ("en", "zh-CN")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CATEGORY = re.compile(r"^[a-z0-9_-]{1,32}$")


def _i18n(value: dict[str, str] | None, *, max_len: int, required: bool = False):
    if value is None:
        return None
    unknown = set(value) - set(LOCALES)
    if unknown:
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    out = {k: (value.get(k) or "").strip() for k in LOCALES}
    if required and not out["en"]:
        raise ValueError("'en' is required")
    if any(len(v) > max_len for v in out.values()):
        raise ValueError(f"at most {max_len} characters")
    return out


class ArticleIn(BaseModel):
    slug: str
    kind: Literal["article", "page"] = "article"
    category: str = "general"
    title_i18n: dict[str, str]
    body_i18n: dict[str, str] = Field(default_factory=dict)
    status: Literal["draft", "published"] = "draft"
    pinned: bool = False

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,62}")
        return v

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if not _CATEGORY.match(v):
            raise ValueError("category must match [a-z0-9_-]{1,32}")
        return v

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=200, required=True)  # type: ignore[return-value]

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=100_000)  # type: ignore[return-value]


class ArticlePatch(BaseModel):
    slug: str | None = None
    kind: Literal["article", "page"] | None = None
    category: str | None = None
    title_i18n: dict[str, str] | None = None
    body_i18n: dict[str, str] | None = None
    status: Literal["draft", "published"] | None = None
    pinned: bool | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is not None and not _SLUG.match(v):
            raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,62}")
        return v

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str | None) -> str | None:
        if v is not None and not _CATEGORY.match(v):
            raise ValueError("category must match [a-z0-9_-]{1,32}")
        return v

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=200, required=True)

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=100_000)


class AnnouncementIn(BaseModel):
    enabled: bool | None = None
    title_i18n: dict[str, str] | None = None
    body_i18n: dict[str, str] | None = None

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=200)

    @field_validator("body_i18n")
    @classmethod
    def _b(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=20_000)


class AutoConfirmIn(BaseModel):
    manual: int = Field(ge=1, le=24 * 90)
    auto_card: int = Field(ge=1, le=24 * 90)


class SiteIn(BaseModel):
    announcement: AnnouncementIn | None = None
    after_sales_default_hours: int | None = Field(None, ge=0, le=8760)
    auto_confirm_hours: AutoConfirmIn | None = None
    support_hours: dict[str, str] | None = None

    @field_validator("support_hours")
    @classmethod
    def _s(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=200)
