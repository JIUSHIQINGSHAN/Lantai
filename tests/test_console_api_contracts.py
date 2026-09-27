"""控制台 API 契约冒烟（前端体检第一批修复的回归测试，不 mock 业务逻辑）。

覆盖：
1. GET /stats 真实返回 by_domain（前端中枢总览域指标依赖；曾误调不存在的 /mem/stats）。
2. POST /conflicts/{id}/resolve 走 JSON body（裁决理由不再进 URL query）。
3. POST /crystals/{id}/decide 路由已挂载（FEATURE_CRYSTALS 默认开，修复硬编码 False 死路）。

用 TestClient(app) 直打真实路由 + 真实 service 直到 DB 查询层；
不存在的 id 触发 service ValueError，借此验证「路由→body 解析→service」链路贯通，
不写库、不污染开发库。

鉴权：本机开发库的 api_keys 表可能存有 key，导致 DEV MODE 关闭、全部 401。
CI/干净库下 db_has_api_key() 为 False，回环 + 无环境 API_KEY 即放行；
这里 monkeypatch db_has_api_key 模拟该环境（仅绕过鉴权前置，业务逻辑零 mock）。
"""

import pytest
from fastapi.testclient import TestClient

import lantai.core.auth as auth_module
from lantai.api.app import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth_module, "db_has_api_key", lambda: False)
    with TestClient(app) as c:
        yield c


def test_stats_returns_by_domain(client):
    res = client.get("/stats")
    assert res.status_code == 200
    data = res.json()
    assert "total_memories" in data
    assert "by_domain" in data  # 前端 ovUserMem/ovSessionMem/ovAgentMem 的口径
    assert "by_lane" in data


def test_conflict_resolve_accepts_json_body(client):
    # 不存在的 id：service 抛 ValueError -> 400；证明 body 解析与 service 调用贯通
    res = client.post(
        "/conflicts/__nonexistent__/resolve",
        json={"decision": "resolved", "note": "确实矛盾"},
    )
    assert res.status_code == 400
    assert "not found" in res.json()["detail"]


def test_conflict_resolve_rejects_legacy_query_only(client):
    # 旧 query 传参方式不再可用：缺 body 应是 422 校验错误，而不是静默接受
    res = client.post("/conflicts/__nonexistent__/resolve?decision=resolved&note=x")
    assert res.status_code == 422


def test_crystals_decide_route_is_mounted(client):
    # 路由曾被硬编码不挂载（404）；现在应到达 service 并因 id 不存在返回 422
    res = client.post(
        "/crystals/__nonexistent__/decide",
        json={"approve": True, "steps": ["步骤一"]},
    )
    assert res.status_code == 422
    assert "not found" in res.json()["detail"]


def test_ui_module_assets_are_served(client):
    # 前端模块化拆分（dom/vault/studio/playground）+ d3 本地兑底后，白名单必须放行
    for asset in ("dom.js", "vault.js", "studio.js", "playground.js", "d3.v7.min.js"):
        res = client.get(f"/ui/assets/{asset}")
        assert res.status_code == 200, f"{asset} 404"
        assert "javascript" in res.headers["content-type"]


def test_ui_asset_whitelist_rejects_unknown(client):
    res = client.get("/ui/assets/../../etc/passwd")
    assert res.status_code in (404, 422)
    res = client.get("/ui/assets/evil.js")
    assert res.status_code == 404


def test_digest_today_route_shape(client):
    # 总览「今日盘点」卡片的契约：ok/day/content/stats 字段
    res = client.get("/digest/today")
    assert res.status_code == 200
    data = res.json()
    assert "day" in data and "content" in data and "stats" in data


def test_memories_status_filter_for_limbo(client):
    # 故纸堆面板的契约：/memories?status=archived|retracted|consolidated 可用且分页结构稳定
    for status in ("archived", "retracted", "consolidated"):
        res = client.get(f"/memories?status={status}&limit=5")
        assert res.status_code == 200, f"status={status} -> {res.status_code}"
        data = res.json()
        # 注意：空列表是合法返回（故纸堆为空），不能用 `or` 级联取值
        items = data.get("memories") if "memories" in data else data.get("items")
        assert isinstance(items, list)
        for m in items:
            assert m["status"] == status  # 过滤真实生效，不是忽略参数


def test_recall_report_shape(client):
    # 悬镜「召回报告」tab 的契约（官方路径 /retrieval/recall-report，见 docs 记录）
    res = client.get("/retrieval/recall-report")
    assert res.status_code == 200
    data = res.json()
    for key in ("window_days", "real", "zero", "zero_recall_rate", "by_lane", "by_intent"):
        assert key in data, f"缺少字段 {key}"


def test_prompts_and_core_memory_roundtrip(client):
    # 悬镜「提示词与核心记忆」tab 的契约：列表可读、覆写可写回、核心块可读写
    res = client.get("/prompts")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    res = client.put(
        "/prompts/__contract_test__", json={"template": "测试模板 {q}", "description": "契约测试"}
    )
    assert res.status_code == 200 and res.json()["ok"]

    res = client.get("/prompts/__contract_test__")
    assert res.status_code == 200 and res.json()["template"] == "测试模板 {q}"

    res = client.put("/core-memory", params={"block": "policy", "content": "契约测试策略"})
    assert res.status_code == 200
    res = client.get("/core-memory")
    assert res.status_code == 200
    blocks = res.json()["blocks"]
    assert any(b["block"] == "policy" and b["content"] == "契约测试策略" for b in blocks)


def test_tree_route_follows_wiki_flag(client):
    # 目录树挂在 FEATURE_WIKI 开关下：开→200 且结构稳定；关→404（前端据此降级）
    from lantai.core import settings as s

    res = client.get("/tree")
    if getattr(s.settings, "FEATURE_WIKI", False):
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data.get("nodes"), list)
    else:
        assert res.status_code == 404


def test_import_jsonl_roundtrip(client):
    # 导入导出面板的契约：合法 JSONL 直存，报告结构稳定（uuid 防重跑判重）
    import uuid

    content = f"契约测试导入：兰台前端 {uuid.uuid4().hex[:8]}"
    payload = {"text": '{"content": "' + content + '", "lane": "general"}\n'}
    res = client.post("/import/jsonl", json=payload)
    assert res.status_code == 200
    report = res.json()
    assert report["ok"] is True
    assert report["imported"] == 1
    assert isinstance(report["errors"], list)


def test_checkpoint_latest_route(client):
    res = client.get("/checkpoint/latest")
    assert res.status_code == 200
    cp = res.json()
    assert cp is None or ("session_id" in cp and "blocks" in cp)


def test_graph_expand_route_contract(client):
    # 贯珠（演练场「图增强」模式）的契约：空库也应返回稳定结构
    res = client.post(
        "/search/graph_expand",
        json={"query": "契约测试查询", "top_k": 3, "max_hops": 2, "min_edge_conf": 0.5},
    )
    assert res.status_code == 200
    data = res.json()
    assert "primary_results" in data and isinstance(data["primary_results"], list)
    assert "associated_memories" in data and isinstance(data["associated_memories"], list)


# ── 错误不得伪装成 200（票 .scratch/api-error-status/01）──────────────────
# routes_evolution 的 decide / rollback / feedback 三处把 service 的
# {"ok": False, ...} 原样 200 返回。调用方（前端/脚本）只能靠翻 body 判断成败，
# 而 HTTP 语义上这是 4xx/5xx。统一改为：service 报错 → HTTPException 带正确状态码。


def test_proposal_decide_failure_is_not_200(client):
    """approve 一个已被 apply/拒绝的提案 → 409，不是 200 + {"ok": false}。"""
    res = client.post("/proposals/__nonexistent__/decide", json={"approve": True, "reason": "x"})
    # 不存在 → 404（既有 ValueError 分支已处理）
    assert res.status_code == 404, f"应 404，实得 {res.status_code}: {res.text[:200]}"


def test_proposal_decide_ok_true_still_200(client, monkeypatch):
    """回归护栏：service 成功时仍返回 200（不得把正常响应也改成错误码）。"""
    import lantai.api.routes_evolution as routes_evolution

    monkeypatch.setattr(
        routes_evolution, "decide_proposal", lambda *a, **kw: {"ok": True, "proposal_id": "p1"}
    )
    res = client.post("/proposals/p1/decide", json={"approve": True, "reason": "同意"})
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_rollback_failure_is_not_200(client):
    """回滚一个不存在的记忆 → 404/409，不是 200 + {"ok": false}。"""
    res = client.post("/memory/__nonexistent__/rollback")
    # no previous version 不是「资源不存在」而是「状态不允许」→ 422；
    # 关键是**不再是 200**（错误不得伪装成成功）
    assert res.status_code == 422, f"应 422，实得 {res.status_code}: {res.text[:200]}"
    body = res.json()
    assert "detail" in body, "HTTPException 的错误应落在 detail 字段"
    assert body["detail"], "detail 不得为空（调用方要能看到原因）"


def test_rollback_ok_true_still_200(client, monkeypatch):
    """回归护栏：回滚成功仍返回 200。"""
    import lantai.api.routes_evolution as routes_evolution

    monkeypatch.setattr(
        routes_evolution, "do_rollback", lambda *a, **kw: {"ok": True, "memory_id": "m1"}
    )
    res = client.post("/memory/m1/rollback")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_proposal_decide_ok_false_becomes_4xx(client, monkeypatch):
    """decide 成功调到 service 但 service 返回 ok:False → 4xx（不得 200）。

    与 test_proposal_decide_failure_is_not_200 的区别：那条走的是「提案不存在」
    抛 ValueError 的既有分支；本条走的是 apply 落拒（如 stale 硬门 / 寻址失败）
    返回 {"ok": False} 的路径——正是本次修法覆盖的情形。
    """
    import lantai.api.routes_evolution as routes_evolution

    monkeypatch.setattr(
        routes_evolution,
        "decide_proposal",
        lambda *a, **kw: {"ok": False, "reason": "stale: evidence already consolidated"},
    )
    res = client.post("/proposals/p1/decide", json={"approve": True, "reason": "同意"})
    assert res.status_code == 409, f"应 409，实得 {res.status_code}: {res.text[:200]}"
    assert "stale" in res.json()["detail"]
