from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.catalog.categories import CATEGORIES

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


class AgentStatusIn(BaseModel):
    status: Literal["online", "away", "on_hold", "offline"]
    message: str | None = Field(None, max_length=200)
