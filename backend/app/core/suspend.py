"""Account suspension (docs/review-v5.1.md §5). A suspension is active while ``suspended_at`` is
set and ``suspended_until`` is empty or in the future; an expired one is lifted on first check."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccountSuspended
from app.models import User


def is_suspended(user: User, now: datetime | None = None) -> bool:
    if user.suspended_at is None:
        return False
    return user.suspended_until is None or user.suspended_until > (
        now or datetime.now(timezone.utc)
    )


def suspended_error(user: User) -> AccountSuspended:
    extra: dict[str, str] = {}
    if user.suspended_until is not None:
        extra["until"] = user.suspended_until.isoformat().replace("+00:00", "Z")
    if user.suspend_reason:
        extra["reason"] = user.suspend_reason
    return AccountSuspended("Your account has been suspended", extra=extra)


async def enforce(db: AsyncSession, user: User) -> None:
    """Raise 403 ``account_suspended`` for a suspended user; lift an expired suspension."""
    if user.suspended_at is None:
        return
    if is_suspended(user):
        raise suspended_error(user)
    user.suspended_at = user.suspended_until = user.suspend_reason = None
    await db.commit()
