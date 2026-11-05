"""Account self-service: profile, credentials, deletion (anonymisation)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import ApiError, Conflict, Forbidden, Validation
from app.core.resources import Resources
from app.core.security import hash_password, verify_password
from app.models import AfterSales, Order, User
from app.modules.auth.schemas import INSTANT_DOMAIN, normalize_email
from app.modules.me.schemas import CredentialsIn, DeleteIn, ProfileIn

UNUSABLE_HASH = "!"  # not a valid Argon2 string: verification always fails
OPEN_ORDER_STATES = ("pending_payment", "paid", "manual_review")
OPEN_TICKET_STATES = ("open", "processing")


class InvalidPassword(ApiError):
    """Current password is wrong"""

    status, code = 403, "invalid_password"


class HasOpenOrders(ApiError):
    """Account has unfinished orders or after-sales tickets"""

    status, code = 409, "has_open_orders"


async def update_profile(db: AsyncSession, user: User, body: ProfileIn, ip: str | None) -> User:
    data = body.model_dump(exclude_unset=True)
    before = {"display_name": user.display_name, "locale": user.locale}
    if data.get("display_name") is not None:
        user.display_name = data["display_name"]
    if data.get("locale") is not None:
        user.locale = data["locale"]
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="me.profile_updated",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        before=before,
        after={"display_name": user.display_name, "locale": user.locale},
        ip=ip,
    )
    await db.commit()
    return user


async def change_credentials(
    db: AsyncSession,
    res: Resources,
    user: User,
    body: CredentialsIn,
    current_token: str | None,
    ip: str | None,
) -> User:
    """Set an email and/or a new password. Instant accounts need no current password."""
    if body.email is None and body.new_password is None:
        raise Validation("email or new_password is required")
    if not user.is_instant:
        ok, _ = await verify_password(user.pw_hash, body.current_password or "")
        if not ok:
            raise InvalidPassword("Current password is incorrect")
    fields: list[str] = []
    if body.email is not None:
        email = normalize_email(body.email)
        if email != user.email:
            taken = await db.scalar(select(User.id).where(User.email == email, User.id != user.id))
            if taken is not None:
                raise Conflict("Email already registered")
            user.email = email
            user.email_verified = False
            fields.append("email")
    if body.new_password is not None:
        user.pw_hash = await hash_password(body.new_password)
        fields.append("password")
    if user.is_instant and not user.email.endswith("@" + INSTANT_DOMAIN):
        user.is_instant = False  # now a regular account (kept out of the instant purge)
        fields.append("is_instant")
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="me.credentials_changed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
        detail={"fields": fields},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise Conflict("Email already registered") from None
    if "password" in fields:
        await res.sessions.revoke_user_sessions(user.id, keep=current_token)
    return user


async def has_open_obligations(db: AsyncSession, user_id: uuid.UUID) -> bool:
    open_order = await db.scalar(
        select(Order.id)
        .where(Order.buyer_id == user_id, Order.status.in_(OPEN_ORDER_STATES))
        .limit(1)
    )
    if open_order is not None:
        return True
    ticket = await db.scalar(
        select(AfterSales.id)
        .where(AfterSales.buyer_id == user_id, AfterSales.status.in_(OPEN_TICKET_STATES))
        .limit(1)
    )
    return ticket is not None


async def anonymize(db: AsyncSession, user: User) -> dict[str, Any]:
    """Irreversibly strip PII from a buyer row (kept for order / ledger integrity)."""
    user.email = f"deleted+{user.id}@invalid"
    user.display_name = "Deleted user"
    user.pw_hash = UNUSABLE_HASH
    user.handle = None
    user.status = "deleted"
    user.is_instant = False
    user.email_verified = False
    await db.execute(
        update(AfterSales)
        .where(AfterSales.buyer_id == user.id, AfterSales.status.notin_(OPEN_TICKET_STATES))
        .values(description="[deleted]")
    )
    return {"status": user.status}


async def delete_account(
    db: AsyncSession, res: Resources, user: User, body: DeleteIn, ip: str | None
) -> None:
    if user.role != "buyer":
        raise Forbidden("Staff accounts cannot be deleted here")
    if not user.is_instant:
        ok, _ = await verify_password(user.pw_hash, body.password or "")
        if not ok:
            raise InvalidPassword("Password is incorrect")
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if await has_open_obligations(db, user.id):
        raise HasOpenOrders("Finish or cancel your open orders and after-sales requests first")
    uid, tid, role = user.id, user.tenant_id, user.role
    await anonymize(db, user)
    await audit(
        db,
        tenant_id=tid,
        action="me.deleted",
        actor_id=uid,
        actor_role=role,
        target=f"user:{uid}",
        ip=ip,
    )
    await db.commit()
    await res.sessions.revoke_user_sessions(uid)
