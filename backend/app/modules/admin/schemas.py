from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.catalog.categories import CATEGORIES
from app.modules.inventory.service import end_of_day

_FILE_URL = re.compile(
    r"^/api/v1/files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
LOCALES = ("en", "zh-CN")


def _i18n(value: dict[str, str], *, max_len: int, required: bool) -> dict[str, str]:
    unknown = set(value) - set(LOCALES)
    if unknown:
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    out = {k: (value.get(k) or "").strip() for k in LOCALES}
    if required and not out["en"]:
        raise ValueError("'en' is required")
    if any(len(v) > max_len for v in out.values()):
        raise ValueError(f"at most {max_len} characters")
    return out


def _images(value: list[str]) -> list[str]:
    if len(value) > 10:
        raise ValueError("at most 10 images")
    for url in value:
        if not _FILE_URL.match(url):
            raise ValueError("images must be uploaded file URLs")
    return value


def _category(value: str) -> str:
    if value not in CATEGORIES:
        raise ValueError("unknown category")
    return value


SEPARATORS = ("----", "|", ",", "\t", ":")


def _redeem_url(value: str | None) -> str | None:
    """https URL (or a site-relative path), at most 500 characters."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if len(v) > 500 or any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in v):
        raise ValueError("redeem_url must be at most 500 characters without whitespace")
    lowered = v.lower()
    if lowered.startswith("https://"):
        host = v[8:].split("/", 1)[0]
        if not host or "@" in host:
            raise ValueError("redeem_url must be a valid https URL")
        return v
    if v.startswith("/") and not v.startswith("//"):
        return v
    raise ValueError("redeem_url must be https:// or a relative path")


def _separator(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in SEPARATORS:
        raise ValueError("delivery_separator must be one of ---- | , tab :")
    return value


def _format(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) > 500 or any(ord(c) < 32 for c in v):
        raise ValueError("delivery_format must be at most 500 printable characters")
    return v or None


class BuyerInputConfig(BaseModel):
    """products.buyer_input: the "Order information" popup (docs/review-v4.md §4)."""

    enabled: bool = False
    required: bool = True
    multiline: bool = True
    max_len: int = Field(2000, ge=1, le=4000)
    title_i18n: dict[str, str] = Field(default_factory=dict)
    prompt_i18n: dict[str, str] = Field(default_factory=dict)
    placeholder_i18n: dict[str, str] = Field(default_factory=dict)

    @field_validator("title_i18n", "prompt_i18n", "placeholder_i18n")
    @classmethod
    def _texts(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=1000, required=False)


class ProductIn(BaseModel):
    title_i18n: dict[str, str]
    description_i18n: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    price: str = Field(max_length=20)
    floor_price: str = Field("0", max_length=20)
    currency: Literal["CNY"] = "CNY"
    delivery_mode: Literal["auto_card", "manual"]
    category: str
    status: Literal["active", "draft"] = "active"
    delivery_format: str | None = None
    delivery_separator: str = "----"
    redeem_url: str | None = None
    usage_i18n: dict[str, str] = Field(default_factory=dict)
    after_sales_hours: int | None = Field(None, ge=0, le=8760)
    group_sort: int = Field(0, ge=-1000, le=1000)
    buyer_input: BuyerInputConfig | None = None

    @field_validator("delivery_format")
    @classmethod
    def _df(cls, v: str | None) -> str | None:
        return _format(v)

    @field_validator("delivery_separator")
    @classmethod
    def _ds(cls, v: str) -> str:
        return _separator(v) or "----"

    @field_validator("redeem_url")
    @classmethod
    def _ru(cls, v: str | None) -> str | None:
        return _redeem_url(v)

    @field_validator("usage_i18n")
    @classmethod
    def _u(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=5000, required=False)

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=200, required=True)

    @field_validator("description_i18n")
    @classmethod
    def _d(cls, v: dict[str, str]) -> dict[str, str]:
        return _i18n(v, max_len=5000, required=False)

    @field_validator("images")
    @classmethod
    def _i(cls, v: list[str]) -> list[str]:
        return _images(v)

    @field_validator("category")
    @classmethod
    def _c(cls, v: str) -> str:
        return _category(v)


class ProductPatch(BaseModel):
    title_i18n: dict[str, str] | None = None
    description_i18n: dict[str, str] | None = None
    images: list[str] | None = None
    price: str | None = Field(None, max_length=20)
    floor_price: str | None = Field(None, max_length=20)
    delivery_mode: Literal["auto_card", "manual"] | None = None
    category: str | None = None
    status: Literal["active", "draft"] | None = None
    delivery_format: str | None = None
    delivery_separator: str | None = None
    redeem_url: str | None = None
    usage_i18n: dict[str, str] | None = None
    after_sales_hours: int | None = Field(None, ge=0, le=8760)
    group_sort: int | None = Field(None, ge=-1000, le=1000)
    buyer_input: BuyerInputConfig | None = None  # explicit null clears the popup

    @field_validator("delivery_format")
    @classmethod
    def _df(cls, v: str | None) -> str | None:
        return _format(v)

    @field_validator("delivery_separator")
    @classmethod
    def _ds(cls, v: str | None) -> str | None:
        return _separator(v)

    @field_validator("redeem_url")
    @classmethod
    def _ru(cls, v: str | None) -> str | None:
        return _redeem_url(v)

    @field_validator("usage_i18n")
    @classmethod
    def _u(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _i18n(v, max_len=5000, required=False)

    @field_validator("title_i18n")
    @classmethod
    def _t(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _i18n(v, max_len=200, required=True)

    @field_validator("description_i18n")
    @classmethod
    def _d(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _i18n(v, max_len=5000, required=False)

    @field_validator("images")
    @classmethod
    def _i(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _images(v)

    @field_validator("category")
    @classmethod
    def _c(cls, v: str | None) -> str | None:
        return None if v is None else _category(v)


class CardsImportIn(BaseModel):
    lines: str = Field(max_length=2_000_000)
    note: str | None = Field(None, max_length=200)
    # ISO datetime, or a bare YYYY-MM-DD = valid through that day (UTC); stored as a datetime
    expires_at: datetime | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("expires_at", mode="before")
    @classmethod
    def _exp(cls, v: object) -> datetime | None:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                if len(v) == 10:
                    out = end_of_day(date.fromisoformat(v))
                else:
                    out = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("expires_at must be an ISO date or datetime") from None
        elif isinstance(v, datetime):
            out = v
        else:
            raise ValueError("expires_at must be an ISO date or datetime")
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        if out <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return out


class CardsFindIn(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=100)
    by: Literal["content", "first_field"]

    @field_validator("lines")
    @classmethod
    def _lines(cls, v: list[str]) -> list[str]:
        if any(len(x) > 600 for x in v):
            raise ValueError("line too long")
        return v


class CardsVoidIn(BaseModel):
    ids: list[uuid.UUID] | None = Field(None, max_length=1000)
    batch_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=64)


class CardReplaceIn(BaseModel):
    code: str = Field(min_length=1, max_length=600)


class AgentStatusIn(BaseModel):
    status: Literal["online", "away", "on_hold", "offline"]
    message: str | None = Field(None, max_length=200)
