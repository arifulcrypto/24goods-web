"""Service period boundaries, authorization, durable reminders and sanitized operations."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.models import Outbox
from app.models.operations import ServiceEntitlement, ServiceReminder, ServiceRenewal
from app.modules.operations import jobs, service
from app.modules.operations.overview import collect
from app.modules.operations.router import router
from tests.helpers import buy, notify_params

API = "/api/v1"


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        ("2025-01-31T10:00:00+00:00", "2025-02-28T10:00:00+00:00"),
        ("2024-01-31T10:00:00+00:00", "2024-02-29T10:00:00+00:00"),
        ("2024-02-29T10:00:00+00:00", "2024-03-29T10:00:00+00:00"),
        ("2026-12-31T10:00:00+00:00", "2027-01-31T10:00:00+00:00"),
    ],
)
def test_calendar_month_clamps(start, expected):
    assert (
        service.period_end(datetime.fromisoformat(start), "calendar_month", 30, "UTC").isoformat()
        == expected
    )


def test_calendar_dst_and_fixed_day_policy():
    start = datetime(2026, 2, 8, 2, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    end = service.period_end(start, "calendar_month", 30, "America/Los_Angeles")
    local = end.astimezone(ZoneInfo("America/Los_Angeles"))
    assert (local.month, local.day, local.hour, local.minute) == (3, 8, 3, 30)
    fixed = service.period_end(start, "fixed_days", 30, "America/Los_Angeles")
    assert fixed - start.astimezone(timezone.utc) == timedelta(days=30)


def test_schedule_local_dates_and_stable_overdue():
    due = datetime(2026, 11, 2, 0, tzinfo=timezone.utc)
    row = SimpleNamespace(
        status="active",
        ends_at=due,
        timezone="America/Los_Angeles",
        email_reminders=True,
        telegram_reminders=False,
    )
    plan = jobs.schedule(row, due + timedelta(days=2))
    assert len(plan) == 5 and len(set(plan)) == 5
    before = next(when for kind, when in plan if kind == "before_1:email")
    local = before.astimezone(ZoneInfo(row.timezone))
    assert (local.day, local.hour) == (31, 9)
    assert jobs.schedule(row, due + timedelta(days=2, hours=1)) == plan


def test_ua_and_fixed_collector_never_expose_environment_or_query(monkeypatch, tmp_path):
    assert "secret-marker" not in service.sanitized_ua(
        "<script> token=secret-marker\nCookie=password"
    )
    assert len(service.sanitized_ua("x" * 1000)) <= 256
    monkeypatch.setenv("APP_PRIVATE_TEST_SECRET", "secret-marker")
    data = collect(str(tmp_path))
    assert "secret-marker" not in json.dumps(data)
    assert data["disk"]["free_bytes"] > 0
    assert data["recovery"]["status"] == "unknown"


@pytest.fixture
async def ops(env):
    # Orchestrator wires production main.py; this keeps the independent module testable.
    if not any(getattr(route, "path", None) == f"{API}/admin/services" for route in env.app.routes):
        env.app.include_router(router, prefix=API)
    return env


async def paid(env, gw, buyer):
    product = await env.product(mode="manual", price=1000, floor=100)
    order = await buy(buyer, product)
    trade = "TOPS" + uuid.uuid4().hex[:12]
    gw.paid[order["order_no"]] = ("10.00", trade)
    response = await env.client(csrf=False).get(
        f"{API}/pay/notify/zpay", params=notify_params(order["order_no"], "10.00", trade_no=trade)
    )
    assert response.text == "success"
    return order


async def active(env, gw):
    admin, owner = await env.login("admin")
    buyer, user = await env.login("buyer")
    order = await paid(env, gw, buyer)
    response = await admin.post(
        f"{API}/admin/services",
        json={
            "buyer_id": str(user.id),
            "order_id": order["id"],
            "owner_id": str(owner.id),
            "label": "Monthly service",
            "email_reminders": True,
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    response = await admin.post(f"{API}/admin/services/{item['id']}/activate", json={})
    assert response.status_code == 200, response.text
    return admin, buyer, user, response.json()


async def test_service_paid_renewal_duplicate_and_cross_tenant(ops, gw):
    env = ops
    admin, buyer, _user, item = await active(env, gw)
    renewal = await paid(env, gw, buyer)
    body = {"order_id": renewal["id"], "reason": "staff_service_completed"}
    results = await asyncio.gather(
        *(admin.post(f"{API}/admin/services/{item['id']}/renewals", json=body) for _ in range(2))
    )
    assert all(result.status_code == 200 for result in results), [r.text for r in results]
    assert results[0].json()["ends_at"] == results[1].json()["ends_at"]
    async with env.api_session() as db:
        assert (
            await db.scalar(
                select(func.count(ServiceRenewal.id)).where(
                    ServiceRenewal.tenant_id == env.tenant_id
                )
            )
            == 1
        )
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.get(f"{API}/admin/services/{item['id']}")).status_code in (401, 404)
    assert (await buyer.get(f"{API}/admin/services")).status_code == 403
    response = await admin.post(
        f"{API}/admin/services/{item['id']}/renewals",
        json={"order_id": item["order_id"], "reason": "reuse"},
    )
    assert response.status_code == 409
    unpaid = await buy(buyer, await env.product(mode="manual", price=1000, floor=100))
    response = await admin.post(
        f"{API}/admin/services/{item['id']}/renewals",
        json={"order_id": unpaid["id"], "reason": "unpaid"},
    )
    assert response.status_code == 409
    other, _other_user = await env.login("buyer")
    wrong = await paid(env, gw, other)
    response = await admin.post(
        f"{API}/admin/services/{item['id']}/renewals",
        json={"order_id": wrong["id"], "reason": "wrong_owner"},
    )
    assert response.status_code == 404


async def test_reminder_schedule_delivery_retry_and_cancellation(ops, gw, monkeypatch):
    env = ops
    admin, _, _, item = await active(env, gw)
    async with env.api_session() as db:
        await db.execute(
            update(ServiceEntitlement)
            .where(
                ServiceEntitlement.id == uuid.UUID(item["id"]),
                ServiceEntitlement.tenant_id == env.tenant_id,
            )
            .values(
                starts_at=service.utcnow() - timedelta(days=40),
                ends_at=service.utcnow() - timedelta(minutes=1),
            )
        )
        await db.commit()
    assert await jobs.schedule_reminders(env.res) == 4
    assert await jobs.schedule_reminders(env.res) == 0
    send = AsyncMock()
    monkeypatch.setattr(jobs, "send_reminder", send)
    assert await jobs.process_reminders(env.res) == 4
    assert send.await_count == 4
    assert await jobs.process_reminders(env.res) == 0
    async with env.api_session() as db:
        assert (
            await db.scalar(
                select(func.count(ServiceReminder.id)).where(
                    ServiceReminder.tenant_id == env.tenant_id, ServiceReminder.status == "sent"
                )
            )
            == 4
        )
    # A new version schedules unique rows, and cancellation invalidates them before send.
    response = await admin.patch(
        f"{API}/admin/services/{item['id']}",
        json={"reason": "change_owner_policy", "grace_days": 2},
    )
    assert response.status_code == 200
    assert await jobs.schedule_reminders(env.res) == 4
    response = await admin.patch(
        f"{API}/admin/services/{item['id']}",
        json={"reason": "cancelled_by_staff", "status": "cancelled"},
    )
    assert response.status_code == 200
    assert await jobs.process_reminders(env.res) == 4
    assert send.await_count == 4
    async with env.api_session() as db:
        assert (
            await db.scalar(
                select(func.count(ServiceReminder.id)).where(
                    ServiceReminder.tenant_id == env.tenant_id,
                    ServiceReminder.status == "cancelled",
                )
            )
            == 4
        )


async def test_uncertain_reminder_lease_and_bounded_retries(ops, gw, monkeypatch):
    env = ops
    _, _, _, item = await active(env, gw)
    async with env.api_session() as db:
        await db.execute(
            update(ServiceEntitlement)
            .where(
                ServiceEntitlement.id == uuid.UUID(item["id"]),
                ServiceEntitlement.tenant_id == env.tenant_id,
            )
            .values(
                starts_at=service.utcnow() - timedelta(days=40),
                ends_at=service.utcnow() - timedelta(minutes=1),
            )
        )
        await db.commit()
    await jobs.schedule_reminders(env.res)
    async with env.api_session() as db:
        ident = await db.scalar(
            select(Outbox.id)
            .where(Outbox.tenant_id == env.tenant_id, Outbox.topic == jobs.TOPIC)
            .order_by(Outbox.id)
            .limit(1)
        )
    send = AsyncMock(side_effect=jobs.ReminderSendError("send_outcome_unknown"))
    monkeypatch.setattr(jobs, "send_reminder", send)
    for _ in range(jobs.MAX_ATTEMPTS):
        async with env.api_session() as db:
            await db.execute(
                update(Outbox)
                .where(Outbox.id == ident, Outbox.tenant_id == env.tenant_id)
                .values(available_at=service.utcnow())
            )
            await db.commit()
        assert await jobs.process_one(env.res, env.tenant_id, ident)
    assert send.await_count == jobs.MAX_ATTEMPTS
    assert not await jobs.process_one(env.res, env.tenant_id, ident)
    async with env.api_session() as db:
        row = await db.scalar(
            select(Outbox).where(Outbox.id == ident, Outbox.tenant_id == env.tenant_id)
        )
        assert row.last_error == "send_outcome_unknown" and row.processed_at is not None


async def test_operations_admin_redaction_scope_and_overview(ops):
    env = ops
    admin, user = await env.login("admin")
    async with env.api_session() as db:
        await service.record_event(
            db,
            tenant_id=env.tenant_id,
            environment="test",
            family="login",
            action="auth.login",
            outcome="denied",
            reason="invalid_credentials",
            actor_id=user.id,
            user_agent="Browser token=secret-marker",
            detail={"password": "secret-marker", "attempt": 1},
        )
        await db.commit()
    response = await admin.get(f"{API}/admin/operations/events?family=login")
    assert response.status_code == 200
    assert "secret-marker" not in response.text and response.json()["total"] == 1
    agent, _ = await env.login("agent")
    assert (await agent.get(f"{API}/admin/operations")).status_code == 403
    response = await admin.get(f"{API}/admin/operations")
    assert response.status_code == 200 and response.json()["database"]["status"] == "ready"
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.get(f"{API}/admin/operations/events")).status_code in (401, 403)


@pytest.mark.parametrize("channel", ["email", "telegram"])
async def test_invalid_reminder_configuration_never_sends(monkeypatch, channel):
    monkeypatch.setattr(jobs, "tenant_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(jobs.mailer, "load_config", AsyncMock(return_value=None))
    monkeypatch.setattr(jobs.secretbox, "get_secret", AsyncMock(return_value=None))
    send = AsyncMock()
    monkeypatch.setattr(jobs.mailer, "send_mail", send)
    res = SimpleNamespace(
        settings=SimpleNamespace(env="test", public_base_url="https://example.test")
    )
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        buyer_id=uuid.uuid4(),
        label="Monthly service",
        ends_at=service.utcnow(),
    )
    reminder = SimpleNamespace(id=uuid.uuid4(), kind=f"expiry:{channel}")
    owner = SimpleNamespace(locale="zh-CN", email="staff@example.test")
    with pytest.raises(jobs.ReminderSendError, match="invalid_configuration"):
        await jobs.send_reminder(res, None, row, reminder, owner)
    send.assert_not_awaited()
