"""格物·因果时序拓扑与因果回溯认知引擎冒烟测试 (v0.35 Ticket 01)

基于真实 in-memory SQLite，不使用 mock。
覆盖：
1. CausalEngine.link_causality 因果边合法校验与落库；
2. CausalEngine.trace_chain 顺向正推与逆向归因多跳推演；
3. CausalEngine.trace_chain 环路安全截断防死循环；
4. CausalEngine.get_provenance 决策归因定位 root_cause 与生成正序时间线；
5. CausalEngine.format_causal_context 结构化 Markdown 提示块生成；
6. 跨租户 / 跨用户行级 ACL 隔离防御。
"""

from datetime import UTC, datetime

import pytest

from lantai.cognition.causal import CausalEngine, CausalHop
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem
from lantai.services.edge_service import add_edge


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_memories(
    param_env,
    items: list[tuple[str, str, str, datetime | None]],
):
    """(id, content, user_id, event_time) 批量写入测试数据库"""
    session_factory, _ = param_env
    with session_factory() as s:
        for mid, content, uid, et in items:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
                    lane="fact",
                    status="active",
                    user_id=uid,
                    event_time=et,
                )
            )
        s.commit()


def test_causal_engine_link_and_trace(param_env):
    """Seam 1: 验证 CausalEngine 建立拓扑与顺反多跳推演。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("node_1", "高并发流量激增", "user_alice", t1),
            ("node_2", "连接池耗尽抛出异常", "user_alice", t2),
            ("node_3", "API服务触发熔断限流", "user_alice", t3),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)

    # 1. 建立正向 causes 与 leads_to 边
    res1 = engine.link_causality("node_1", "node_2", relation="causes", confidence=0.95)
    assert res1["relation"] == "causes"

    res2 = engine.link_causality("node_2", "node_3", relation="leads_to", confidence=0.9)
    assert res2["relation"] == "leads_to"

    # 非法因果关系校验
    with pytest.raises(ValueError, match="非法因果关系"):
        engine.link_causality("node_1", "node_2", relation="invalid_rel")

    # 2. 顺向推演
    fwd_hops = engine.trace_chain("node_1", direction="forward")
    assert len(fwd_hops) == 2
    assert isinstance(fwd_hops[0], CausalHop)
    assert fwd_hops[0].from_id == "node_1"
    assert fwd_hops[0].to_id == "node_2"
    assert fwd_hops[1].from_id == "node_2"
    assert fwd_hops[1].to_id == "node_3"

    # 3. 逆向归因
    bwd_hops = engine.trace_chain("node_3", direction="backward")
    assert len(bwd_hops) == 2
    assert bwd_hops[0].from_id == "node_3"
    assert bwd_hops[0].to_id == "node_2"
    assert bwd_hops[1].from_id == "node_2"
    assert bwd_hops[1].to_id == "node_1"


def test_causal_engine_provenance_and_context(param_env):
    """Seam 2: 验证决策归因、时间线与提示词上下文生成。"""
    t1 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("cause_root", "上线新版无锁算法", "user_alice", t1),
            ("decision_target", "系统吞吐量提升300%", "user_alice", t2),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)
    engine.link_causality("cause_root", "decision_target", relation="causes", confidence=0.98)

    # 1. 执行溯源
    prov = engine.get_provenance(memory_id="decision_target")
    assert prov["found"] is True
    assert prov["target_id"] == "decision_target"
    assert prov["root_cause_id"] == "cause_root"
    assert "上线新版无锁算法" in prov["narrative"]
    assert len(prov["timeline"]) == 2

    # 2. 上下文 Markdown 格式化
    ctx = engine.format_causal_context("decision_target")
    assert "【格物·因果决策脉络】" in ctx
    assert "decision_target" in ctx
    assert "上线新版无锁算法" in ctx

    # 3. 查询事件时间线
    timeline = engine.get_timeline("decision_target")
    assert len(timeline) >= 2


def test_causal_engine_cycle_and_acl_isolation(param_env):
    """Seam 3: 验证环路安全截断与租户/用户隔离。"""
    _seed_memories(
        param_env,
        [
            ("c_1", "环路A", "user_alice", None),
            ("c_2", "环路B", "user_alice", None),
            ("c_3", "环路C", "user_alice", None),
            ("bob_node", "Bob私人数据", "user_bob", None),
        ],
    )
    alice = _principal("user_alice")
    engine_alice = CausalEngine(principal=alice)

    # 构造环: c_1 -> c_2 -> c_3 -> c_1
    engine_alice.link_causality("c_1", "c_2", "causes")
    engine_alice.link_causality("c_2", "c_3", "causes")
    engine_alice.link_causality("c_3", "c_1", "causes")

    # 环路阻断测试（不陷入死循环）
    chain = engine_alice.trace_chain("c_1", direction="forward", max_depth=10)
    assert len(chain) == 2  # 抵达 c_1 时安全终止

    # ACL 隔离测试：Alice 无法越权访问 Bob 的私人节点
    add_edge("bob_node", "c_1", relation="causes", confidence=0.9, principal=None)
    bwd = engine_alice.trace_chain("bob_node", direction="backward")
    assert len(bwd) == 0

    prov = engine_alice.get_provenance(memory_id="bob_node")
    assert prov["found"] is False
