from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.content.schemas_v5 import (
    AuditIn,
    DefaultsIn,
    DeliveryIn,
    EmailIn,
    EmailTemplatesIn,
    FeaturesIn,
    GeneralIn,
    ImportIn,
    LegalIn,
    NotificationsIn,
    ResellersIn,
    SecurityIn,
    SeoIn,
)

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


class ChannelIn(BaseModel):
    enabled: bool | None = None
    surcharge_permille: int | None = Field(None, ge=0, le=200)


class PaymentsIn(BaseModel):
    channels: dict[str, ChannelIn] = Field(default_factory=dict)

    @field_validator("channels")
    @classmethod
    def _known(cls, v: dict[str, ChannelIn]) -> dict[str, ChannelIn]:
        from app.modules.payments.channels import LABELS

        if set(v) - set(LABELS):
            raise ValueError("unknown payment channel")
        return v


class SupportIn(BaseModel):
    welcome_i18n: dict[str, str] | None = None
    quick_questions_i18n: dict[str, list[str]] | None = None

    @field_validator("welcome_i18n")
    @classmethod
    def _w(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=500)

    @field_validator("quick_questions_i18n")
    @classmethod
    def _q(cls, v: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if v is None:
            return None
        if set(v) - set(LOCALES):
            raise ValueError("only 'en' and 'zh-CN' keys are allowed")
        out: dict[str, list[str]] = {}
        for k in LOCALES:
            items = [(q or "").strip() for q in v.get(k, [])]
            items = [q for q in items if q]
            if len(items) > 6:
                raise ValueError("at most 6 quick questions per locale")
            if any(len(q) > 60 for q in items):
                raise ValueError("each quick question is at most 60 characters")
            out[k] = items
        return out


class RateLimitIn(BaseModel):
    enabled: bool | None = None
    limit: int | None = Field(None, ge=10, le=10000)
    window_s: int | None = Field(None, ge=1, le=3600)


class AfterSalesDefaultIn(BaseModel):
    unit: Literal["month", "hour"]
    value: int = Field(ge=0, le=8760)

    @model_validator(mode="after")
    def _range(self) -> AfterSalesDefaultIn:
        if self.unit == "month" and not 1 <= self.value <= 12:
            raise ValueError("months must be 1..12")
        return self


class SiteIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # v5 sections (review-v5 §3); each is a partial update stored via settings_v5.merge
    general: GeneralIn | None = None
    legal: LegalIn | None = None
    features: FeaturesIn | None = None
    security: SecurityIn | None = None
    defaults: DefaultsIn | None = None
    email: EmailIn | None = None
    notifications: NotificationsIn | None = None
    email_templates: EmailTemplatesIn | None = None
    delivery: DeliveryIn | None = None
    import_: ImportIn | None = Field(None, alias="import")
    resellers: ResellersIn | None = None
    audit: AuditIn | None = None
    seo: SeoIn | None = None
    announcement: AnnouncementIn | None = None
    payments: PaymentsIn | None = None
    support: SupportIn | None = None
    rate_limit: RateLimitIn | None = None
    after_sales_default_hours: int | None = Field(None, ge=0, le=8760)  # legacy
    after_sales_default: AfterSalesDefaultIn | None = None
    auto_confirm_hours: AutoConfirmIn | None = None
    support_hours: dict[str, str] | None = None

    @field_validator("support_hours")
    @classmethod
    def _s(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return _i18n(v, max_len=200)
