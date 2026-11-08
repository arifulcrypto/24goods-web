"""Small unprivileged collectors: fixed proc/cgroup paths and allowlisted recovery metadata."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import anyio.to_thread
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.resources import Resources
from app.models import Order, Outbox
from app.modules.operations.service import utcnow

STARTED = time.monotonic()
CACHE_S = 30
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
RECOVERY_PATH = Path("/run/24goods-operations/status.json")


def _bounded(path: Path, limit: int = 16384) -> str:
    with path.open() as file:
        value = file.read(limit + 1)
    if len(value) > limit:
        raise ValueError("collector snapshot too large")
    return value


def _integer(path: str) -> int | None:
    try:
        return int(_bounded(Path(path), 100).strip())
    except (OSError, ValueError):
        return None


def collect(upload_dir: str) -> dict[str, Any]:
    """Label Linux /proc memory as host-visible; the cgroup is the API's own budget."""
    memory: dict[str, int] | None = None
    try:
        fields = dict(
            re.findall(
                r"^(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)",
                _bounded(Path("/proc/meminfo")),
                re.MULTILINE,
            )
        )
        memory = {key: int(value) * 1024 for key, value in fields.items()}
    except (OSError, ValueError):
        pass
    disk = None
    try:
        usage = shutil.disk_usage(upload_dir)
        stat = os.statvfs(upload_dir)
        disk = {
            "scope": "uploads_filesystem",
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "free_percent": round(100 * usage.free / usage.total, 1) if usage.total else None,
            "free_inodes": stat.f_favail,
            "total_inodes": stat.f_files,
        }
    except OSError:
        pass
    recovery: dict[str, Any] = {
        "status": "unknown",
        "last_complete_at": None,
        "offsite_verified": None,
        "last_restore_test_at": None,
    }
    try:
        source = json.loads(_bounded(RECOVERY_PATH))
        if isinstance(source, dict):
            for key in ("last_complete_at", "last_restore_test_at"):
                value = source.get(key)
                if isinstance(value, str) and re.fullmatch(r"[0-9T:Z+. -]{10,40}", value):
                    recovery[key] = value
            if isinstance(source.get("offsite_verified"), bool):
                recovery["offsite_verified"] = source["offsite_verified"]
            recovery["status"] = (
                "reported"  # collector-reported evidence; not a restore certification
            )
    except (OSError, ValueError):
        pass
    try:
        load = list(os.getloadavg())
    except OSError:
        load = None
    return {
        "load": {"scope": "host_visible", "averages": load},
        "host_visible_memory": memory,
        "process": {
            "rss_bytes": _rss(),
            "uptime_seconds": int(time.monotonic() - STARTED),
            "cpu_seconds": round(time.process_time(), 3),
        },
        "cgroup": {
            "memory_current": _integer("/sys/fs/cgroup/memory.current"),
            "memory_max": _integer("/sys/fs/cgroup/memory.max"),
        },
        "disk": disk,
        "recovery": recovery,
    }


def _rss() -> int | None:
    try:
        found = re.search(r"^VmRSS:\s+(\d+)", _bounded(Path("/proc/self/status")), re.MULTILINE)
        return int(found[1]) * 1024 if found else None
    except (OSError, ValueError):
        return None


async def overview(db: AsyncSession, res: Resources, tenant: Any) -> dict[str, Any]:
    cache_key = str(res.settings.upload_dir)
    cached = _CACHE.get(cache_key)
    if cached is None or time.monotonic() - cached[0] >= CACHE_S:
        values = await anyio.to_thread.run_sync(collect, cache_key)
        _CACHE.clear()  # one process configuration; never grow a user-controlled cache
        _CACHE[cache_key] = (time.monotonic(), values)
    else:
        values = cached[1]
    database = {"status": "unknown", "schema_version": None}
    outbox: dict[str, Any] = {"pending": None, "oldest_at": None, "failed": None}
    review = None
    try:
        await db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        database = {
            "status": "ready",
            "schema_version": None,
        }
        count, oldest = (
            await db.execute(
                select(func.count(Outbox.id), func.min(Outbox.created_at)).where(
                    Outbox.tenant_id == tenant, Outbox.processed_at.is_(None)
                )
            )
        ).one()
        failed = await db.scalar(
            select(func.count(Outbox.id)).where(
                Outbox.tenant_id == tenant, Outbox.last_error.is_not(None)
            )
        )
        outbox = {
            "pending": count,
            "oldest_at": oldest.isoformat() if oldest else None,
            "failed": failed,
        }
        review = await db.scalar(
            select(func.count(Order.id)).where(
                Order.tenant_id == tenant, Order.status == "manual_review"
            )
        )
    except Exception:
        await db.rollback()
        database["status"] = "unavailable"
    redis: dict[str, Any] = {"status": "unknown", "used_memory": None, "maxmemory": None}
    try:
        await asyncio.wait_for(res.redis.ping(), 1.0)
        redis["status"] = "ready"
        try:
            memory = await asyncio.wait_for(res.redis.info("memory"), 1.0)
            redis.update(
                {
                    key: memory[key]
                    for key in ("used_memory", "maxmemory")
                    if isinstance(memory.get(key), int)
                }
            )
        except Exception:
            redis["metrics_status"] = "restricted"  # ACL may intentionally deny INFO
    except Exception:
        redis["status"] = "unavailable"
    release = os.environ.get("APP_RELEASE_SHA", "")
    digest = os.environ.get("APP_IMAGE_DIGEST", "")
    return {
        "collected_at": utcnow().isoformat(),
        "environment": res.settings.env,
        "release": {
            "commit": release if re.fullmatch(r"[0-9a-f]{40}", release) else None,
            "image_digest": digest if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else None,
        },
        "resources": values,
        "database": database,
        "redis": redis,
        "outbox": outbox,
        "payment_review": {"orders": review},
        "delivery": {"status": "unknown"},
    }
