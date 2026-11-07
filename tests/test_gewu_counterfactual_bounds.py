"""格物反事实遍历设界冒烟测试（票 39，真库真引擎不 mock）。

病灶（.scratch/v0.68-next-window-2026-11/issues/39-causal-counterfactual-path-blowup.md）：
- `evaluate_counterfactual` 逆向回溯是**全简单路径枚举**：per-path visited 防环
  （不死循环），但队列按路径分支复制、`surviving_paths` 无上限——n 层 k 叉
  跨层全连接菱形的简单路径数 ≈ k^n；
- `_get_parent_hops` 每个队列项各开新 SQLite 会话——路径数 × 深度次会话开合；
- 入口两端无界：REST `CounterfactualReq.max_depth` 裸 int（10**9 合法），
  MCP `handle_causal_counterfactual` 裸 `int(params.get(...))` 同；
- `find_critical_causal_path` 同款 per-path 枚举且产品代码零调用方（死代码，
  票面授权「补帽 + 留痕」处置）。

复杂度锁口径：票面 TDD 1 明言「计时断言不做（脆弱），以调用计数为复杂度锁」
——本文件以 `db.get_session` **开合计数**为锁（修复前菱形图 120+ 次，修复后
常数次），确定性、不依赖机器速度（与 .scratch/watch/
build-context-2s-timeout-flaky.md 登记的机器速度敏感 flaky 家族划清界限）。

测试纪律（AGENTS.md）：真私有内存库 + 真 MemoryItem 行 + 真
CausalEngine.link_causality 建边 + 真 CausalEngine 直调（或真 handle()
JSON-RPC 分发）；`db.get_session` 的计数包装是**透传探针**（内部仍调真工厂、
返回真会话），不 mock 被测链路的任何内部逻辑。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.api.app import app
from lantai.cognition.causal import CausalEngine
from lantai.cli.mcp import handle
from lantai.core.auth import Principal, get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

pytestmark = pytest.mark.mcp_full_surface()

_FAN = 3
_LAYERS = 4  # 简单路径数 = fan**layers = 81；修复后应 ≤ 路径帽（票面建议 64）

# 语义对照臂的小图（与既有 engine smoke 同构）：root→trigger→target 与 alt→target
_SMALL_GRAPH = {
    "mem_root": ["mem_trigger"],
    "mem_alt": ["mem_target"],
    "mem_trigger": ["mem_target"],
}


def _principal(user_id: str, *, admin: bool = False) -> Principal:
    return Principal(user_id=user_id, allowed_lanes=None, role="admin" if admin else "user")


@pytest.fixture()
def cf_env(monkeypatch):
    """私有内存库 + db.get_session 开合透传计数探针（真工厂真会话，仅计数）。"""
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    counter = {"opens": 0}

    def counting_factory():
        counter["opens"] += 1
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", counting_factory)
    yield SimpleNamespace(engine=engine, counter=counter)
    engine.dispose()


def _seed_node(session: Session, node_id: str) -> None:
    session.add(
        MemoryItem(
            id=node_id,
            memory_type="semantic",
            key=f"k-{node_id}",
            content=f"反事实设界样本 {node_id}",
            lane="fact",
            status="active",
            user_id=None,
            event_time=utcnow(),
        )
    )


def _build_diamond(session: Session) -> str:
    """4 层 3 叉跨层全连接菱形：T ← A×3 ← B×3 ← C×3 ← D×3（D 为根，无父边）。

    真建边走 CausalEngine.link_causality（与 REST/MCP 建边面同链路）。
    返回 target 节点 ID。
    """
    layers: list[list[str]] = [["cfb-T"]]
    for li in range(_LAYERS):
        layers.append([f"cfb-{'ABCD'[li]}{i}" for i in range(_FAN)])
    for row in layers:
        for node_id in row:
            _seed_node(session, node_id)
    session.commit()

    engine = CausalEngine(principal=None)
    for upper, lower in zip(layers, layers[1:]):
        for parent in lower:
            for child in upper:
                engine.link_causality(
                    source_id=parent, target_id=child, relation="causes", confidence=0.9
                )
    return layers[0][0]


def _build_small_graph(session: Session) -> None:
    for node_id in ("mem_root", "mem_trigger", "mem_target", "mem_alt"):
        _seed_node(session, node_id)
    session.commit()
    engine = CausalEngine(principal=None)
    for parent, children in _SMALL_GRAPH.items():
        for child in children:
            engine.link_causality(
                source_id=parent, target_id=child, relation="causes", confidence=0.9
            )


# ---------------------------------------------------------------------------
# 主臂：菱形复杂度锁（路径帽 + 会话开合计数）
# ---------------------------------------------------------------------------


def test_counterfactual_paths_capped_on_diamond(cf_env):
    """主臂（票面 TDD 1）：4 层 3 叉菱形上反事实枚举必须有界且外显截断口径。

    修复前双红：81 条简单路径全部收进 alternative_paths（> 帽），且每个队列项
    各开一个 SQLite 会话（121 次开合随路径数指数放大）。修复后路径数 ≤ 帽、
    results 带 paths_truncated 标注、全程常数次会话开合（复杂度锁，非计时）。
    """
    from lantai.cognition.causal import COUNTERFACTUAL_PATH_CAP

    with Session(cf_env.engine) as session:
        target = _build_diamond(session)

    cf_env.counter["opens"] = 0  # 建图开销不计入，只锁推演遍历本身
    engine = CausalEngine(principal=None)
    res = engine.evaluate_counterfactual(
        target_id=target,
        intervened_node_id="cfb-decoy",  # 不在图上的介入节点 → 全部路径存活
        max_depth=10,
    )

    assert res["found"] is True
    assert res["is_necessary"] is False
    assert res["reachable_without_node"] is True
    assert len(res["alternative_paths"]) <= COUNTERFACTUAL_PATH_CAP, (
        f"路径枚举无帽：{len(res['alternative_paths'])} 条（fan**layers=81）"
    )
    assert res.get("paths_truncated") is True, "超帽必须外显 paths_truncated（宁脏不瞒）"
    assert cf_env.counter["opens"] <= 4, (
        f"会话开合未与路径数解耦：{cf_env.counter['opens']} 次（修复前 ≈ 队列项数 121）"
    )


def test_find_critical_path_capped_on_diamond(cf_env):
    """死代码帽臂：find_critical_causal_path 同款全路径枚举必须补帽留痕。

    票面处置授权「删除或补帽 + 留痕」二选一；本仓 ADR-0061 记其为已交付能力、
    CHANGELOG 有历史条目，取补帽 + paths_truncated 留痕（删除另需 ADR 增补，
    留待维护者若后续裁决删除时一并做）。修复前红：结果无截断口径、81 路全枚举。
    """
    with Session(cf_env.engine) as session:
        target = _build_diamond(session)

    cf_env.counter["opens"] = 0
    engine = CausalEngine(principal=None)
    res = engine.find_critical_causal_path(target_id=target, max_depth=10)

    assert res["found"] is True
    assert res["critical_path"], "菱形图上必须给出关键路径"
    assert len(res["critical_path"]) <= 10
    assert res.get("paths_truncated") is True, "超帽枚举必须外显 paths_truncated"


# ---------------------------------------------------------------------------
# 边界锁：REST 422 / MCP -32602 / 引擎直调 ValueError（单一真源三面镜像）
# ---------------------------------------------------------------------------


def test_engine_rejects_out_of_range_depth(cf_env):
    """钳制臂（引擎单一真源）：max_depth 越界必须显式拒绝，不许静默当默认值用。

    修复前红：0/负数直接空转（BFS 首轮即深度截断，返回「必要根因」的假结论）、
    超大值（10**9）合法接受——深度无界即路径枚举无界的一半。
    """
    from lantai.cognition.causal import COUNTERFACTUAL_MAX_DEPTH_LIMIT

    engine = CausalEngine(principal=None)
    for bad in (0, -1, COUNTERFACTUAL_MAX_DEPTH_LIMIT + 1, 10**9):
        with pytest.raises(ValueError, match="max_depth"):
            engine.evaluate_counterfactual(
                target_id="cfb-T", intervened_node_id="cfb-x", max_depth=bad
            )
        with pytest.raises(ValueError, match="max_depth"):
            engine.find_critical_causal_path(target_id="cfb-T", max_depth=bad)


def test_rest_counterfactual_rejects_unbounded_depth(cf_env):
    """REST 边界锁：max_depth 越界 422（pydantic ge/le），合法上界 200。

    修复前红：裸 int 零校验，0/负数/10**9 全部 200 直通 service 层。
    """
    from lantai.cognition.causal import COUNTERFACTUAL_MAX_DEPTH_LIMIT

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: _principal("root", admin=True)
    try:
        for bad in (0, -1, COUNTERFACTUAL_MAX_DEPTH_LIMIT + 1, 10**9):
            resp = client.post(
                "/edges/counterfactual",
                json={
                    "target_memory_id": "cfb-T",
                    "intervened_node_id": "cfb-x",
                    "max_depth": bad,
                },
            )
            assert resp.status_code == 422, f"max_depth={bad} 未被入口拒绝：{resp.status_code}"

        resp = client.post(
            "/edges/counterfactual",
            json={
                "target_memory_id": "cfb-T",
                "intervened_node_id": "cfb-x",
                "max_depth": COUNTERFACTUAL_MAX_DEPTH_LIMIT,
            },
        )
        assert resp.status_code == 200
        resp.json()  # 合法上界照常出结果帧（引擎语义由对照臂锁，这里只锁入口契约）
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mcp_counterfactual_rejects_unbounded_depth(cf_env):
    """MCP 边界锁：真 handle() JSON-RPC 分发，非法 max_depth 收 -32602 错误帧。

    修复前红：裸 int(params.get) 零校验，越界参数原样透传进无界枚举。
    """
    from lantai.cognition.causal import COUNTERFACTUAL_MAX_DEPTH_LIMIT

    for bad in (0, -1, COUNTERFACTUAL_MAX_DEPTH_LIMIT + 1, 10**9):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "causal_counterfactual",
                "arguments": {
                    "target_memory_id": "cfb-T",
                    "intervened_node_id": "cfb-x",
                    "max_depth": bad,
                },
            },
        }
        resp = handle(req)
        assert resp is not None and "error" in resp, f"max_depth={bad} 被静默接受"
        assert resp["error"]["code"] == -32602, f"拒绝语义不是 invalid params：{bad}"


def test_mcp_counterfactual_valid_depth_roundtrip(cf_env):
    """MCP 入口对照臂：合法上界调用照常返回结果帧（拒绝面不得误伤正常调用）。"""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "causal_counterfactual",
            "arguments": {
                "target_memory_id": "cfb-none",
                "intervened_node_id": "cfb-none-2",
                "max_depth": 10,
            },
        },
    }
    resp = handle(req)
    assert resp is not None and "result" in resp
    assert "error" not in resp
    data = json.loads(resp["result"]["content"][0]["text"])
    assert "is_necessary" in data  # 空库 + 特权 principal 的既有语义：照常出推演帧
    assert data.get("paths_truncated", False) is False


# ---------------------------------------------------------------------------
# 对照臂：帽内小图语义零变化（帽与会话复用不得改写既有反事实结论）
# ---------------------------------------------------------------------------


def test_small_graph_alternative_path_semantics_unchanged(cf_env):
    """语义回归臂 1：阻断 trigger 后 alt 支路仍是唯一替代路径，逐跳一致且未截断。"""
    with Session(cf_env.engine) as session:
        _build_small_graph(session)

    cf_env.counter["opens"] = 0
    res = CausalEngine(principal=None).evaluate_counterfactual(
        target_id="mem_target", intervened_node_id="mem_trigger", max_depth=10
    )
    assert res["found"] is True
    assert res["is_necessary"] is False
    assert res["reachable_without_node"] is True
    assert res.get("paths_truncated") is False
    assert len(res["alternative_paths"]) == 1
    hops = res["alternative_paths"][0]
    assert [(h["from_id"], h["to_id"], h["relation"]) for h in hops] == [
        ("mem_alt", "mem_target", "causes")
    ]


def test_small_graph_necessary_root_semantics_unchanged(cf_env):
    """语义回归臂 2：单链图阻断唯一根因仍是 is_necessary=True、零替代路径、未截断。"""
    with Session(cf_env.engine) as session:
        for node_id in ("solo_root", "solo_target"):
            _seed_node(session, node_id)
        session.commit()
        CausalEngine(principal=None).link_causality(
            source_id="solo_root", target_id="solo_target", relation="causes", confidence=0.9
        )

    res = CausalEngine(principal=None).evaluate_counterfactual(
        target_id="solo_target", intervened_node_id="solo_root", max_depth=10
    )
    assert res["found"] is True
    assert res["is_necessary"] is True
    assert res["reachable_without_node"] is False
    assert res["alternative_paths"] == []
    assert res.get("paths_truncated") is False
