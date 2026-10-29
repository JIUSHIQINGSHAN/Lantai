"""格物·因果时序拓扑与事件溯源链冒烟测试 (Ticket 01)

基于真实 in-memory SQLite，不使用 mock。
覆盖：
1. EdgeRelation 枚举与 CAUSAL_RELATIONS 常量完整性；
2. MemoryEdge 对 causes / triggered_by / leads_to 的合法落盘与读取；
3. 向后兼容：原有谓词 supports / contradicts / refines / supersedes 正常工作；
4. 多跳因果拓扑溯源：顺查（causes / leads_to）与逆查（triggered_by）；
5. 环路安全退出与租户 ACL 隔离保护。
"""

import pytest
from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user
from lantai.models.enums import EdgeRelation
from lantai.models.tables import CAUSAL_RELATIONS, MemoryItem
from lantai.services.edge_service import add_edge, get_causal_chain, list_edges
from lantai.storage.edges import get_causal_chain as storage_get_causal_chain


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_memories(param_env, items: list[tuple[str, str, str]]):
    """(id, content, user_id) 批量灌入真实内存数据库"""
    session_factory, _ = param_env
    with session_factory() as s:
        for mid, content, uid in items:
            s.add(
                MemoryItem(
                    id=mid,
                    memory_type="semantic",
                    key=f"k-{mid}",
                    content=content,
                    lane="fact",
                    status="active",
                    user_id=uid,
                )
            )
        s.commit()


def test_causal_relations_enum_and_constants():
    """Seam 1: 枚举定义与常量完整性"""
    assert EdgeRelation.CAUSES == "causes"
    assert EdgeRelation.TRIGGERED_BY == "triggered_by"
    assert EdgeRelation.LEADS_TO == "leads_to"

    # 向后兼容枚举
    assert EdgeRelation.SUPPORTS == "supports"
    assert EdgeRelation.CONTRADICTS == "contradicts"
    assert EdgeRelation.REFINES == "refines"
    assert EdgeRelation.SUPERSEDES == "supersedes"

    # CAUSAL_RELATIONS 常量集合
    assert "causes" in CAUSAL_RELATIONS
    assert "triggered_by" in CAUSAL_RELATIONS
    assert "leads_to" in CAUSAL_RELATIONS
    assert len(CAUSAL_RELATIONS) == 3


def test_causal_edge_persistence_and_compatibility(param_env):
    """Seam 2: 因果边落库与向后兼容校验"""
    _seed_memories(
        param_env,
        [
            ("mem_cause_1", "数据库连接池耗尽", "user_alice"),
            ("mem_effect_1", "API网关返回504超限", "user_alice"),
            ("mem_legacy_1", "既有前置记忆", "user_alice"),
        ],
    )
    user = _principal("user_alice")

    # 创建因果边
    edge_causes = add_edge(
        "mem_cause_1",
        "mem_effect_1",
        relation="causes",
        confidence=0.9,
        principal=user,
    )
    assert edge_causes["edge_id"].startswith("edge_")
    assert edge_causes["relation"] == "causes"

    # 创建兼容旧关系边
    edge_supports = add_edge(
        "mem_legacy_1",
        "mem_cause_1",
        relation="supports",
        confidence=0.7,
        principal=user,
    )
    assert edge_supports["relation"] == "supports"

    # 查询关系
    edges_res = list_edges("mem_cause_1", principal=user)
    relations = {e["relation"] for e in edges_res["edges"]}
    assert "causes" in relations
    assert "supports" in relations


def test_causal_chain_forward(param_env):
    """Seam 3: 正向因果拓扑推演 (causes / leads_to)"""
    _seed_memories(
        param_env,
        [
            ("event_a", "用户输入密码错误多次", "user_alice"),
            ("event_b", "触发安全风控锁定策略", "user_alice"),
            ("event_c", "账户状态被置为锁定", "user_alice"),
        ],
    )
    user = _principal("user_alice")
    add_edge("event_a", "event_b", relation="causes", confidence=0.95, principal=user)
    add_edge("event_b", "event_c", relation="leads_to", confidence=0.88, principal=user)

    chain_res = get_causal_chain("event_a", direction="forward", principal=user)
    chain = chain_res["chain"]
    assert len(chain) == 2
    assert chain[0]["from_id"] == "event_a"
    assert chain[0]["to_id"] == "event_b"
    assert chain[0]["relation"] == "causes"
    assert chain[1]["from_id"] == "event_b"
    assert chain[1]["to_id"] == "event_c"
    assert chain[1]["relation"] == "leads_to"


def test_causal_chain_backward_provenance(param_env):
    """Seam 4: 逆向诱因归因溯源 (triggered_by 与 逆向 causes)"""
    _seed_memories(
        param_env,
        [
            ("root_bug", "内存泄漏导致GC挂起", "user_alice"),
            ("mid_crash", "Worker进程健康检查失败", "user_alice"),
            ("final_outage", "服务集群不可用警报", "user_alice"),
        ],
    )
    user = _principal("user_alice")
    # mid_crash triggered_by root_bug
    add_edge("mid_crash", "root_bug", relation="triggered_by", confidence=0.9, principal=user)
    # root_bug causes mid_crash (另一个方向的causes也能逆向追溯)
    # final_outage triggered_by mid_crash
    add_edge("final_outage", "mid_crash", relation="triggered_by", confidence=0.85, principal=user)

    chain_res = get_causal_chain("final_outage", direction="backward", principal=user)
    chain = chain_res["chain"]
    assert len(chain) == 2
    assert chain[0]["from_id"] == "final_outage"
    assert chain[0]["to_id"] == "mid_crash"
    assert chain[0]["relation"] == "triggered_by"
    assert chain[1]["from_id"] == "mid_crash"
    assert chain[1]["to_id"] == "root_bug"


def test_causal_chain_cycle_prevention_and_acl(param_env):
    """Seam 5: 环路拦截与端到端 API 路由及多租户 ACL 隔离"""
    from lantai.api.app import app

    _seed_memories(
        param_env,
        [
            ("loop_1", "环路节点1", "user_alice"),
            ("loop_2", "环路节点2", "user_alice"),
            ("loop_3", "环路节点3", "user_alice"),
            ("secret_node", "Bob的机密诱因", "user_bob"),
        ],
    )
    user_alice = _principal("user_alice")

    # 构造 loop_1 -> loop_2 -> loop_3 -> loop_1 环状因果图
    add_edge("loop_1", "loop_2", relation="causes", confidence=0.8, principal=user_alice)
    add_edge("loop_2", "loop_3", relation="causes", confidence=0.8, principal=user_alice)
    add_edge("loop_3", "loop_1", relation="causes", confidence=0.8, principal=user_alice)

    # 验证底层 storage_get_causal_chain 防死循环（环路在闭合处安全终止）
    raw_chain = storage_get_causal_chain("loop_1", direction="forward", max_depth=10)
    assert len(raw_chain) == 2  # loop_1 -> loop_2 -> loop_3 (到达loop_1时被visited拦截终止)

    # 验证 API 路由
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: user_alice
    try:
        resp = client.get("/edges/loop_1/causal-chain?direction=forward")
        assert resp.status_code == 200
        data = resp.json()
        assert "chain" in data
        assert len(data["chain"]) == 2

        # 验证 ACL 隔离：Alice 无法获取包含 Bob 记忆的因果边
        # 建立由 secret_node(Bob) -> loop_1(Alice) 的边
        add_edge("secret_node", "loop_1", relation="causes", confidence=0.9, principal=None)
        resp_bob = client.get("/edges/secret_node/causal-chain?direction=forward")
        assert resp_bob.status_code == 200
        # Bob 的节点对 Alice 不可见，导致 Alice 无法以 secret_node 遍历
        assert len(resp_bob.json()["chain"]) == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
