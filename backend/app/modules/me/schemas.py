from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProfileIn(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=40)
    locale: Literal["en", "zh-CN"] | None = None

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        if not v or any(ord(c) < 32 for c in v):
            raise ValueError("invalid display name")
        return v


class CredentialsIn(BaseModel):
    current_password: str | None = Field(None, max_length=128)
    new_password: str | None = Field(None, min_length=10, max_length=128)


class EmailChangeIn(BaseModel):
    email: str = Field(max_length=254)
    current_password: str | None = Field(None, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        from app.modules.auth.schemas import normalize_email

        return normalize_email(v)


class EmailChangeConfirmIn(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=12)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        from app.modules.auth.schemas import normalize_email

        return normalize_email(v)


class DeleteIn(BaseModel):
    password: str | None = Field(None, max_length=128)
    confirm: Literal["DELETE"]


class AvatarPresetIn(BaseModel):
    preset: Literal[
        "p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08",
        "p09", "p10", "p11", "p12", "p13", "p14", "p15", "p16",
    ]  # fmt: skip
