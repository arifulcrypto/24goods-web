"""Canned CS replies: shared ones any staff member may use, personal ones only their owner.

Templates are plain text that staff paste into the composer; they are never sent on their own
and carry no buyer data, so they hold no secrets. Every write is tenant-filtered and audited.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Forbidden, NotFound
from app.models import CsReplyTemplate, User

MAX_PER_OWNER = 60


def view(row: CsReplyTemplate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "body": row.body,
        "sort": row.sort,
        "shared": row.owner_id is None,
    }


async def listing(db: AsyncSession, staff: User) -> list[dict[str, Any]]:
    """Shared templates plus the caller's own; never another agent's personal ones."""
    rows = await db.scalars(
        select(CsReplyTemplate)
        .where(
            CsReplyTemplate.tenant_id == staff.tenant_id,
            or_(CsReplyTemplate.owner_id.is_(None), CsReplyTemplate.owner_id == staff.id),
        )
        .order_by(
            CsReplyTemplate.owner_id.is_(None).desc(),
            CsReplyTemplate.sort,
            CsReplyTemplate.created_at,
        )
        .limit(2 * MAX_PER_OWNER)
    )
    return [view(r) for r in rows]


async def _owned(db: AsyncSession, staff: User, ref: uuid.UUID) -> CsReplyTemplate:
    row = await db.scalar(
        select(CsReplyTemplate).where(
            CsReplyTemplate.id == ref, CsReplyTemplate.tenant_id == staff.tenant_id
        )
    )
    if row is None:
        raise NotFound("Template not found")
    # A shared template is listed for everyone, so refusing it may say why. Another agent's
    # private one is never listed, so it stays absent rather than confirming the id exists.
    if row.owner_id is None:
        if staff.role != "admin":
            raise Forbidden("Only an administrator can change a shared reply")
    elif row.owner_id != staff.id:
        raise NotFound("Template not found")
    return row


async def create(
    db: AsyncSession,
    staff: User,
    *,
    title: str,
    body: str,
    shared: bool,
    ip: str | None,
) -> dict[str, Any]:
    if shared and staff.role != "admin":
        raise Forbidden("Only an administrator can add a shared reply")
    owner_id = None if shared else staff.id
    count = len(
        list(
            await db.scalars(
                select(CsReplyTemplate.id)
                .where(
                    CsReplyTemplate.tenant_id == staff.tenant_id,
                    CsReplyTemplate.owner_id.is_(None)
                    if shared
                    else CsReplyTemplate.owner_id == staff.id,
                )
                .limit(MAX_PER_OWNER + 1)
            )
        )
    )
    if count > MAX_PER_OWNER:
        raise Forbidden("The reply template limit is reached; delete one first")
    row = CsReplyTemplate(
        tenant_id=staff.tenant_id,
        owner_id=owner_id,
        title=title,
        body=body,
        sort=count,
        created_by=staff.id,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="cs_template.created",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"cs_reply_template:{row.id}",
        after={"title": title, "shared": shared},
        ip=ip,
    )
    await db.commit()
    return view(row)


async def update(
    db: AsyncSession,
    staff: User,
    ref: uuid.UUID,
    *,
    title: str,
    body: str,
    ip: str | None,
) -> dict[str, Any]:
    row = await _owned(db, staff, ref)
    before = {"title": row.title}
    row.title, row.body = title, body
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="cs_template.updated",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"cs_reply_template:{row.id}",
        before=before,
        after={"title": title},
        ip=ip,
    )
    await db.commit()
    return view(row)


async def remove(db: AsyncSession, staff: User, ref: uuid.UUID, ip: str | None) -> None:
    row = await _owned(db, staff, ref)
    title = row.title
    await db.execute(
        delete(CsReplyTemplate).where(
            CsReplyTemplate.id == ref, CsReplyTemplate.tenant_id == staff.tenant_id
        )
    )
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="cs_template.deleted",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"cs_reply_template:{ref}",
        before={"title": title},
        ip=ip,
    )
    await db.commit()
