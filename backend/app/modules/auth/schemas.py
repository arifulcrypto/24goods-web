from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.pow import PowIn

HANDLE_RE = re.compile(r"^u-[a-z2-7]{7}$")
INSTANT_DOMAIN = "instant.invalid"

_EMAIL = re.compile(
    r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def normalize_email(value: str) -> str:
    v = value.strip().lower()
    if len(v) > 254 or not _EMAIL.match(v):
        raise ValueError("invalid email")
    if v.endswith(".invalid"):  # reserved for instant-account placeholders / deleted users
        raise ValueError("invalid email")
    return v


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=40)
    locale: Literal["en", "zh-CN"] = "en"
    pow: PowIn | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v or any(ord(c) < 32 for c in v):
            raise ValueError("invalid display name")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=254)  # an email address OR an instant-account handle (u-xxxxxxx)
    password: str = Field(min_length=1, max_length=128)
    pow: PowIn | None = None


class InstantIn(BaseModel):
    # No email here: an unverified address on a throw-away account would let anyone squat
    # someone else's address. Instant accounts add one later via /me/credentials.
    pow: PowIn | None = None
