"""Canned CS replies and the role-aware presence default (owner request, v6)."""

TPL = "/api/v1/admin/reply-templates"


async def _create(client, title="Delivery time", body="On its way.", **kw):
    return await client.post(TPL, json={"title": title, "body": body, **kw})


async def test_agent_keeps_personal_replies_and_sees_shared_ones(env):
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")
    other, _ = await env.login("agent")

    assert (
        await _create(admin, "House rules", "Please read the rules.", shared=True)
    ).status_code == 201
    mine = await _create(agent, "My greeting", "Hi there!")
    assert mine.status_code == 201, mine.text
    assert mine.json()["shared"] is False

    titles = {r["title"] for r in (await agent.get(TPL)).json()}
    assert titles == {"House rules", "My greeting"}
    # another agent sees the shared reply only: personal replies never cross staff accounts
    assert {r["title"] for r in (await other.get(TPL)).json()} == {"House rules"}


async def test_only_an_admin_can_create_or_change_a_shared_reply(env):
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")

    assert (await _create(agent, "Sneaky", "Shared please", shared=True)).status_code == 403
    created = await _create(admin, "Shared note", "Team wide", shared=True)
    ref = created.json()["id"]
    blocked = await agent.put(f"{TPL}/{ref}", json={"title": "Hijacked", "body": "No"})
    assert blocked.status_code == 403
    assert (await agent.delete(f"{TPL}/{ref}")).status_code == 403
    assert (
        await admin.put(f"{TPL}/{ref}", json={"title": "Renamed", "body": "Team wide"})
    ).status_code == 200


async def test_one_agent_cannot_touch_another_agents_reply(env):
    agent, _ = await env.login("agent")
    other, _ = await env.login("agent")
    ref = (await _create(agent, "Mine", "Private text")).json()["id"]

    # never listed for them, so it stays absent rather than confirming the id exists
    assert (
        await other.put(f"{TPL}/{ref}", json={"title": "Stolen", "body": "x"})
    ).status_code == 404
    assert (await other.delete(f"{TPL}/{ref}")).status_code == 404
    assert (await agent.delete(f"{TPL}/{ref}")).status_code == 204
    assert (await agent.get(TPL)).json() == []


async def test_a_reply_is_invisible_across_tenants(env):
    from sqlalchemy import text

    admin, _ = await env.login("admin")
    ref = (await _create(admin, "Shared", "Tenant one", shared=True)).json()["id"]

    # same host maps to the default tenant: an other-tenant staff session is rejected outright
    outsider, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await outsider.get(TPL)).status_code == 401
    # RLS: the other tenant cannot read or delete the row even by id
    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT count(*) FROM cs_reply_templates")) == 0
        assert (
            await db.scalar(text("SELECT count(*) FROM cs_reply_templates WHERE id=:i"), {"i": ref})
            == 0
        )
    # the owning tenant still has it
    assert [r["title"] for r in (await admin.get(TPL)).json()] == ["Shared"]


async def test_blank_and_oversized_replies_are_rejected(env):
    admin, _ = await env.login("admin")
    assert (await _create(admin, "   ", "body")).status_code == 422
    assert (await _create(admin, "title", "   ")).status_code == 422
    assert (await _create(admin, "title", "x" * 2001)).status_code == 422


async def test_a_buyer_cannot_reach_reply_templates(env):
    buyer, _ = await env.login("buyer")
    assert (await buyer.get(TPL)).status_code in (401, 403)
    assert (await _create(buyer)).status_code in (401, 403)


async def test_presence_defaults_to_busy_for_support_and_offline_for_admin(env):
    """Neither role is ever presented as online before anyone sets a status."""
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")

    assert (await agent.get("/api/v1/admin/agent/status")).json()["status"] == "on_hold"
    assert (await admin.get("/api/v1/admin/agent/status")).json()["status"] == "offline"
    listed = {
        r["display_name"]: r["status"] for r in (await admin.get("/api/v1/admin/agents")).json()
    }
    assert listed == {"Admin": "offline", "Agent": "on_hold"}
