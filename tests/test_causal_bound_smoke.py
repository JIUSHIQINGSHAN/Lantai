"""票 39：格物反事实全简单路径枚举 + max_depth 无界——菱形因果图查询风暴。

`.scratch/v0.68-next-window-2026-11/issues/39-causal-counterfactual-path-blowup.md`

**先说影响**：`evaluate_counterfactual` 逆向回溯是**全简单路径枚举**——防环
只有 per-path visited（不死循环，但队列按路径分支复制）、`surviving_paths`
无上限、每队列项 `_get_parent_hops` 各开新 DB session（路径数 × 深度次
SQLite 会话开合）。链式菱形图（7 环 ×3 叉）简单路径数 ≈ 3^7 = 2187；
REST/MCP `max_depth` 双端无界（`10**9` 合法）。任何认证用户自建菱形图后
单请求即可拖垮 SQLite 后端——图检索其余三面均已有界，格物反事实是唯一
裸奔的遍历面。

**修法**：路径帽 `_COUNTERFACTUAL_PATH_CAP=64` + 扩展预算
`_COUNTERFACTUAL_NODE_BUDGET=512`（双界先到先停 + `paths_truncated` 标注，
口径同拾遗 `scan_truncated` partial 语义；预算同时即 DB 会话数复杂度锁）；
BFS 共享单 session（消除路径数 × 深度次开合）；REST `max_depth`
`ge=1, le=20`（`ProjectImpactReq`/`min_confidence` 同面同钳）+ MCP 同步
ValueError；死代码 `find_critical_causal_path`（生产零调用、smoke 测试在用）
补同款帽 + docstring 留痕（票面二选一取补帽）。

**测试边界声明**：真内存库 + 真 `link_causality`（引擎真实建边面）+ 真
`evaluate_counterfactual` 直调零 mock；会话计数经 `causal.db.get_session`
包一层计数委托（统计口径 = 被测函数的真实 session 开合数，非 mock 被测
链路）；计时断言不做（票面口径），以调用计数为复杂度锁。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.mcp_full_surface()

FAN = 3
DIAMONDS = 7  # 链式 3 叉菱形 ×7 → 简单路径 3^7 = 2187（帽 64 / 预算 512 之外）


def _build_chain_diamonds(diamonds: int) -> tuple[list[str], list[str]]:
    """真建边面构造链式 k 叉菱形：hub_i →(causes) mid_i{k} →(causes) hub_{i+1}。

    返回 (全部节点 id, hub 节点 id 列表)。mid_id[i][k] 为第 i 环第 k 叉。
    """
    from lantai.cognition.causal import CausalEngine
    from lantai.models.tables import MemoryItem
    from lantai.storage import db

    hubs: list[str] = []
    mids: list[list[str]] = []
    with db.get_session() as s:
        for i in range(diamonds + 1):
            hid = f"mem-39-hub-{i}"
            s.add(MemoryItem(id=hid, content=f"枢纽 {i}", key=f"hub-{i}", status="active"))
            hubs.append(hid)
        for i in range(diamonds):
            row: list[str] = []
            for k in range(FAN):
                mid = f"mem-39-mid-{i}-{k}"
                s.add(
                    MemoryItem(id=mid, content=f"分支 {i}-{k}", key=f"mid-{i}-{k}", status="active")
                )
                row.append(mid)
            mids.append(row)
        s.commit()

    engine = CausalEngine()
    for i in range(diamonds):
        for k in range(FAN):
            out = engine.link_causality(source_id=hubs[i], target_id=mids[i][k], relation="causes")
            assert out.get("status") == "ok" or out.get("edge_id"), out
            out = engine.link_causality(
                source_id=mids[i][k], target_id=hubs[i + 1], relation="causes"
            )
            assert out.get("status") == "ok" or out.get("edge_id"), out
    return hubs, [h for h in hubs]


class TestCounterfactualBound:
    """TDD 1 主臂：链式菱形图路径数/会话数必须被帽与预算封顶。"""

    def test_path_and_session_bounds(self, param_env):
        import lantai.cognition.causal as causal_mod

        hubs, _ = _build_chain_diamonds(DIAMONDS)

        # 计数包装用测试体内 MonkeyPatch.context（块退出先于任何 fixture 收尾，
        # 确定性还原——conftest 模块卫生哨兵对 db.get_session 身份有绊线）
        real_get_session = causal_mod.db.get_session
        counter = {"n": 0}

        def counting_get_session(*a, **kw):
            counter["n"] += 1
            return real_get_session(*a, **kw)

        with pytest.MonkeyPatch.context() as mp2:
            mp2.setattr(causal_mod.db, "get_session", counting_get_session)
            engine = causal_mod.CausalEngine()
            # 介入第 0 环其中一叉：max_depth=10 跳数内仍可枚举 3^5 = 243 条
            # 替代路径（红测实测；帽 64 之外。深度更长路径本就受 max_depth 截断）
            res = engine.evaluate_counterfactual(
                target_id=hubs[-1],
                intervened_node_id="mem-39-mid-0-0",
                max_depth=10,
            )
        assert res.get("found") is True
        paths = res.get("alternative_paths") or []
        assert len(paths) <= 64, f"路径数未被帽封顶：{len(paths)}（3^7 链式菱形指数枚举实锤）"
        assert res.get("paths_truncated") is True, "截断标注缺失（partial 语义）"
        assert counter["n"] <= 600, (
            f"DB 会话开合数未被预算封顶：{counter['n']}（路径数 × 深度次开合实锤）"
        )

    def test_small_graph_semantics_unchanged(self, param_env):
        """TDD 3 对照臂：小菱形（3 环 ×3 叉）路径全枚举结论与修复前一致。"""
        hubs, _ = _build_chain_diamonds(3)
        from lantai.cognition.causal import CausalEngine

        res = CausalEngine().evaluate_counterfactual(
            target_id=hubs[-1],
            intervened_node_id="mem-39-mid-0-0",
            max_depth=10,
        )
        # 3^3 = 27，介入一叉 → 2×3^2 = 18 条全枚举（帽/预算之内零截断）
        assert res.get("is_necessary") is False
        assert len(res.get("alternative_paths") or []) == 18, res.get("narrative")


class TestRestDepthBound:
    """TDD 2：REST max_depth 边界锁——0/负数/超大 422，合法值照常。"""

    def test_counterfactual_depth_bounds(self, param_env):
        from lantai.api.app import app

        client = TestClient(app)
        for bad in (0, -1, 10**9):
            r = client.post(
                "/edges/counterfactual",
                json={
                    "target_memory_id": "mem-39-hub-0",
                    "intervened_node_id": "mem-39-mid-0-0",
                    "max_depth": bad,
                },
                headers={"X-API-Key": "test"},
            )
            assert r.status_code == 422, f"max_depth={bad} 修复前放行：{r.status_code}"
        ok = client.post(
            "/edges/counterfactual",
            json={
                "target_memory_id": "mem-39-hub-0",
                "intervened_node_id": "mem-39-mid-0-0",
                "max_depth": 10,
            },
            headers={"X-API-Key": "test"},
        )
        assert ok.status_code == 200, f"合法 max_depth 不得误伤：{ok.status_code}"

    def test_project_impact_bounds(self, param_env):
        from lantai.api.app import app

        client = TestClient(app)
        r = client.post(
            "/edges/project-impact",
            json={"memory_id": "mem-39-hub-0", "max_depth": 0},
            headers={"X-API-Key": "test"},
        )
        assert r.status_code == 422, f"project-impact max_depth=0 修复前放行：{r.status_code}"


class TestMcpDepthBound:
    """TDD 2 续：MCP 面同步钳制——越界 ValueError（→ -32602）。"""

    def test_counterfactual_depth_bounds(self, param_env):
        from lantai.cli.mcp import handle_causal_counterfactual

        for bad in (0, -1, 10**9):
            with pytest.raises(ValueError):
                handle_causal_counterfactual(
                    {
                        "target_memory_id": "mem-39-hub-0",
                        "intervened_node_id": "mem-39-mid-0-0",
                        "max_depth": bad,
                    }
                )

    def test_project_impact_bounds(self, param_env):
        from lantai.cli.mcp import handle_causal_project_impact

        with pytest.raises(ValueError):
            handle_causal_project_impact({"memory_id": "mem-39-hub-0", "max_depth": 0})
