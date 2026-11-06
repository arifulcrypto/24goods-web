from __future__ import annotations


async def test_categories_localized(env):
    c = env.client()
    en = (await c.get("/api/v1/categories")).json()
    zh = (await c.get("/api/v1/categories", headers={"Accept-Language": "zh-CN,zh;q=0.9"})).json()
    assert {"slug": "gift_cards", "key": "gift_cards", "name": "Gift cards", "icon": None} in en
    assert {"slug": "gift_cards", "key": "gift_cards", "name": "礼品卡", "icon": None} in zh
    assert (await c.get("/api/v1/categories?lang=zh-CN")).json() == zh


async def test_list_search_and_detail(env):
    steam = await env.product(
        title="Steam Wallet Code", zh="Steam 钱包充值码", category="gift_cards"
    )
    await env.product(
        mode="manual",
        title="Custom Logo Design",
        zh="定制标志设计",
        category="design_services",
        desc="Vector logo by a pro designer",
    )
    await env.product(title="Hidden draft", zh="草稿", status="draft")
    await env.product(title="Other tenant item", tenant_id=env.other_tenant_id)
    await env.import_codes(steam, ["S-1", "S-2", "S-3"])
    c = env.client()

    page = (await c.get("/api/v1/products")).json()
    assert page["total"] == 2 and page["page"] == 1 and page["page_size"] == 20
    by_title = {p["title"]: p for p in page["items"]}
    assert by_title["Steam Wallet Code"]["stock"] == 3
    assert by_title["Custom Logo Design"]["stock"] is None
    assert by_title["Steam Wallet Code"]["price"] == "19.90"

    for q, expected in [
        ("steam", "Steam Wallet Code"),
        ("wallet", "Steam Wallet Code"),
        ("钱包", "Steam Wallet Code"),
        ("标志", "Custom Logo Design"),
        ("vector", "Custom Logo Design"),
        ("stea", "Steam Wallet Code"),
    ]:
        items = (await c.get("/api/v1/products", params={"q": q})).json()["items"]
        assert [i["title"] for i in items] == [expected], q
    assert (await c.get("/api/v1/products", params={"q": "100%_"})).json()["total"] == 0
    items = (await c.get("/api/v1/products", params={"category": "design_services"})).json()
    assert items["total"] == 1

    d = (await c.get(f"/api/v1/products/{steam.id}?lang=zh-CN")).json()
    assert d["title"] == "Steam 钱包充值码" and d["description"] == "即时发货"
    assert d["status"] == "active" and d["images"] == []
    r = await c.get("/api/v1/products/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_support_status_best_agent(env):
    c = env.client()
    assert (await c.get("/api/v1/support/status")).json()["status"] == "offline"
    a1, _ = await env.login("agent")
    a2, _ = await env.login("agent")
    assert (await a1.put("/api/v1/admin/agent/status", json={"status": "away"})).status_code == 200
    r = await a2.put("/api/v1/admin/agent/status", json={"status": "online", "message": "Hi"})
    assert r.json()["status"] == "online"
    s = (await c.get("/api/v1/support/status")).json()
    assert s["status"] == "online" and s["message"] == "Hi"
    agents = (await a1.get("/api/v1/admin/agents")).json()
    assert sorted(a["status"] for a in agents) == ["away", "online"]
    assert (await a1.get("/api/v1/admin/agent/status")).json()["status"] == "away"
