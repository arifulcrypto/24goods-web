"""#72 A1: one periodic-jobs pass at a time across API replicas."""

from __future__ import annotations

from app import main


async def test_jobs_pass_is_skipped_while_another_process_holds_the_lock(env):
    await env.res.redis.set(main.JOBS_LOCK_KEY, "other-replica", ex=60)
    assert await main.run_jobs_once(env.res, hourly=False) is False
    assert await env.res.redis.get(main.JOBS_LOCK_KEY) in (b"other-replica", "other-replica")


async def test_jobs_pass_takes_and_releases_the_lock(env):
    await env.res.redis.delete(main.JOBS_LOCK_KEY)
    assert await main.run_jobs_once(env.res, hourly=False) is True
    assert await env.res.redis.get(main.JOBS_LOCK_KEY) is None
