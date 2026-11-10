"""Account self-service: profile, credentials, deletion (anonymisation)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import apitoken
from app.core.audit import audit
from app.core.errors import ApiError, Conflict, Forbidden, Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.security import STAFF_ROLES, hash_password, verify_password
from app.models import AfterSales, Attachment, Conversation, Message, Order, User
from app.modules.auth import codes
from app.modules.auth.schemas import INSTANT_DOMAIN
from app.modules.me.schemas import CredentialsIn, DeleteIn, ProfileIn
from app.modules.uploads.processing import Processed
from app.modules.uploads.storage import FileStore

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
    """Set a new password. The email changes only through the confirmed-code flow below."""
    if body.new_password is None:
        raise Validation("new_password is required")
    if not user.is_instant:
        ok, _ = await verify_password(user.pw_hash, body.current_password or "")
        if not ok:
            raise InvalidPassword("Current password is incorrect")
    fields: list[str] = []
    if body.new_password is not None:
        user.pw_hash = await hash_password(body.new_password)
        await apitoken.revoke_user_tokens(db, user.tenant_id, user.id)
        fields.append("password")
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
    await db.commit()
    await res.sessions.revoke_user_sessions(user.id, keep=current_token)
    return user


async def confirm_email_change(
    db: AsyncSession,
    res: Resources,
    user: User,
    email: str,
    code: str,
    ip: str | None,
    current_token: str | None = None,
    lang: str = "en",
) -> User:
    """Set the login email once the code mailed to it is proven (single use, 5 attempts).
    A regular account's other sessions are signed out and the previous address is told."""
    await enforce(res.redis, "me:email_confirm", str(user.id), 20, 3600)
    ident = codes.email_change_ident(user.id, email)
    if not await codes.verify(res.redis, res.settings, user.tenant_id, ident, "email_change", code):
        raise codes.InvalidCode("The verification code is invalid or expired")
    taken = await db.scalar(select(User.id).where(User.email == email, User.id != user.id))
    if taken is not None:
        raise Conflict("This email can't be used")
    before = {
        "email": user.email,
        "email_verified": user.email_verified,
        "is_instant": user.is_instant,
    }
    user.email = email
    user.email_verified = True
    if user.is_instant and not email.endswith("@" + INSTANT_DOMAIN):
        user.is_instant = False  # now a regular account (kept out of the instant purge)
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="me.email_changed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        before=before,
        after={"email": user.email, "email_verified": True, "is_instant": user.is_instant},
        ip=ip,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise Conflict("This email can't be used") from None
    if not before["is_instant"]:
        await res.sessions.revoke_user_sessions(user.id, keep=current_token)
    await codes.notify_email_changed(db, res, user.tenant_id, str(before["email"]), lang)
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


async def anonymize(db: AsyncSession, user: User) -> tuple[dict[str, Any], list[str]]:
    """Irreversibly strip PII from a buyer row (kept for order / ledger integrity).

    Also redacts the user's chat messages (text -> NULL, ``payload={"redacted": true}``; the
    row stays so conversations keep their order), marks their uploads deleted and clears the
    descriptions of their closed after-sales tickets. Returns ``(summary, storage_keys)``:
    the caller deletes those files from the store AFTER the commit. Audit-log IPs are kept.
    """
    user.email = f"deleted+{user.id}@invalid"
    user.display_name = "Deleted user"
    user.pw_hash = UNUSABLE_HASH
    user.handle = None
    user.status = "deleted"
    user.is_instant = False
    user.email_verified = False
    # order information can hold session tokens: drop it with the account
    await db.execute(update(Order).where(Order.buyer_id == user.id).values(buyer_input=None))
    await db.execute(
        update(AfterSales)
        .where(AfterSales.buyer_id == user.id, AfterSales.status.notin_(OPEN_TICKET_STATES))
        .values(description="[deleted]")
    )
    await db.execute(
        update(Message)
        .where(Message.sender_id == user.id, Message.sender_role == "buyer")
        .values(text=None, payload={"redacted": True})
    )
    keys = list(
        await db.scalars(
            select(Attachment.storage_key).where(
                Attachment.owner_id == user.id, Attachment.deleted_at.is_(None)
            )
        )
    )
    if user.avatar_key:
        keys.append(user.avatar_key)
        user.avatar_key = None
    await db.execute(
        update(Attachment)
        .where(Attachment.owner_id == user.id, Attachment.deleted_at.is_(None))
        .values(deleted_at=datetime.now(timezone.utc))
    )
    return {"status": user.status}, keys


async def remove_files(store: FileStore, keys: list[str]) -> None:
    for key in keys:
        try:
            await store.delete(key)
        except OSError:  # best effort: the row is already marked deleted and serves 404
            continue


async def delete_account(
    db: AsyncSession,
    res: Resources,
    user: User,
    body: DeleteIn,
    ip: str | None,
    store: FileStore,
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
    _, keys = await anonymize(db, user)
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
    await remove_files(store, keys)
    await res.sessions.revoke_user_sessions(uid)


async def set_avatar(
    db: AsyncSession, store: FileStore, user: User, processed: Processed, ip: str | None
) -> User:
    # serialize concurrent uploads by the same user so no key is orphaned; drop the new file if
    # the commit fails
    await db.execute(
        select(User.id)
        .where(User.id == user.id, User.tenant_id == user.tenant_id)
        .with_for_update()
    )
    await db.refresh(user, ["avatar_key"])
    key = await store.write(processed.data)
    old = user.avatar_key
    user.avatar_key = key
    try:
        await _avatar_set_audit(db, user, processed, ip)
        await db.commit()
    except BaseException:
        await remove_files(store, [key])
        raise
    if old:
        await remove_files(store, [old])
    return user


async def _avatar_set_audit(
    db: AsyncSession, user: User, processed: Processed, ip: str | None
) -> None:
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="me.avatar_set",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
        detail={"bytes": len(processed.data), "mime": processed.mime},
    )


async def clear_avatar(db: AsyncSession, store: FileStore, user: User, ip: str | None) -> User:
    old = user.avatar_key
    user.avatar_key = None
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="me.avatar_removed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
    )
    await db.commit()
    if old:
        await remove_files(store, [old])
    return user


async def avatar_for_viewer(db: AsyncSession, target_id: uuid.UUID, viewer: User) -> str | None:
    """Storage key of ``target_id``'s avatar if ``viewer`` may see it, else None (-> 404).

    Same tenant only. A user sees their own; staff see every avatar of their tenant; a buyer
    sees a staff avatar only when that staff member wrote in one of the buyer's conversations.
    """
    target = await db.scalar(
        select(User).where(User.id == target_id, User.tenant_id == viewer.tenant_id)
    )
    if target is None or not target.avatar_key or target.status == "deleted":
        return None
    if target.id == viewer.id or viewer.role in STAFF_ROLES:
        return target.avatar_key
    if target.role in STAFF_ROLES:
        chatted = await db.scalar(
            select(
                exists().where(
                    Message.sender_id == target.id,
                    Message.tenant_id == viewer.tenant_id,
                    Message.conversation_id == Conversation.id,
                    Conversation.tenant_id == viewer.tenant_id,
                    Conversation.buyer_id == viewer.id,
                )
            )
        )
        if chatted:
            return target.avatar_key
    return None
