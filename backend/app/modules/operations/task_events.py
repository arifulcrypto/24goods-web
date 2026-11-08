"""Bounded platform task heartbeats without raw errors or business payloads."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.core.db import tenant_session
from app.core.resources import Resources
from app.models import Tenant
from app.modules.operations.service import record_event


async def report(res: Resources, name: str, started: float, failed: bool) -> None:
    code = name.lower().replace(" ", "_")[:48]
    # A job failure may repeat, but must not turn a broken loop into a log flood.
    key = "sec:task-heartbeat:" + code + (":failed" if failed else ":ok")
    if not await res.redis.set(key, "1", nx=True, ex=60 if failed else 300):
        return
    async with tenant_session(res.maker, None) as db:
        tid = await db.scalar(select(Tenant.id).where(Tenant.kind == "platform").limit(1))
    if tid is None:
        return
    async with tenant_session(res.maker, tid) as db:
        await record_event(
            db,
            tenant_id=tid,
            environment=res.settings.env,
            family="task",
            action="job." + code,
            outcome="failed" if failed else "success",
            reason="job_failed" if failed else "heartbeat",
            detail={"duration_ms": int((time.monotonic() - started) * 1000)},
        )
        await db.commit()
