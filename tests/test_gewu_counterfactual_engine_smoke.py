"""格物·核心反事实与因果推演认知引擎升级冒烟测试 (v0.40 Ticket 01)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用 mock。
覆盖公共接口 Seams：
1. evaluate_counterfactual: 单点必要根因阻断判定 (is_necessary=True, no alternative path)；
2. evaluate_counterfactual: 存在冗余替代路径时的反事实判定 (is_necessary=False, alternative_paths 捕获备用支路)；
3. project_causal_impact: 前瞻决策影响面模拟与置信度衰减传播（防环路死锁，返回 impacted_nodes 及 cumulative_confidence）；
4. find_critical_causal_path: 最强关键路径量化（从上游根因到目标后果的最高置信度因果链）；
5. 租户行级 ACL 严格隔离防护：不可见节点不可穿透与推演。
"""

from datetime import UTC, datetime

import pytest

from lantai.cognition.causal import CausalEngine, CausalHop
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem


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
    """(id, content, user_id, event_time) 批量写入真实测试数据库"""
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


def test_evaluate_counterfactual_necessary_root(param_env):
    """Seam 1: 验证反事实介入判定——当某节点是达成目标的唯一必要因果节点时，
    若阻断该节点，反事实推演判定 is_necessary=True，且下游因果链完全断裂。
    """
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("cause_root", "上线未经加固的未签名脚本", "user_alice", t1),
            ("step_trigger", "注入恶意执行载荷", "user_alice", t2),
            ("target_breach", "核心数据库被越权转储", "user_alice", t3),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)

    engine.link_causality("cause_root", "step_trigger", relation="causes", confidence=0.9)
    engine.link_causality("step_trigger", "target_breach", relation="leads_to", confidence=0.95)

    # 1. 介入阻断 step_trigger
    res = engine.evaluate_counterfactual(
        target_id="target_breach", intervened_node_id="step_trigger"
    )
    assert res["target_id"] == "target_breach"
    assert res["intervened_node_id"] == "step_trigger"
    assert res["is_necessary"] is True
    assert res["reachable_without_node"] is False
    assert len(res.get("alternative_paths", [])) == 0
    assert "断裂" in res.get("narrative", "") or "必要" in res.get("narrative", "")


def test_evaluate_counterfactual_with_alternative_path(param_env):
    """Seam 2: 验证多路径下的反事实判定——当存在替代路径时，阻断该节点依然可通过另一分支达成目标，
    此时 is_necessary=False，并输出存活的替代因果路径。
    """
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
    t4 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("root_decision", "采用弱口令初始配置", "user_alice", t1),
            ("path_a_leak", "公网爆破嗅探到密码", "user_alice", t2),
            ("path_b_backup", "备份文件未加密泄漏密钥", "user_alice", t3),
            ("target_login", "攻击者成功登录管理控制台", "user_alice", t4),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)

    # 分支 A
    engine.link_causality("root_decision", "path_a_leak", relation="causes", confidence=0.8)
    engine.link_causality("path_a_leak", "target_login", relation="leads_to", confidence=0.9)

    # 分支 B (替代路径)
    engine.link_causality("root_decision", "path_b_backup", relation="causes", confidence=0.7)
    engine.link_causality("path_b_backup", "target_login", relation="leads_to", confidence=0.85)

    # 介入阻断 path_a_leak
    res = engine.evaluate_counterfactual(target_id="target_login", intervened_node_id="path_a_leak")
    assert res["target_id"] == "target_login"
    assert res["intervened_node_id"] == "path_a_leak"
    assert res["is_necessary"] is False
    assert res["reachable_without_node"] is True
    alt_paths = res.get("alternative_paths", [])
    assert len(alt_paths) >= 1
    # 验证替代路径包含 path_b_backup
    first_alt = alt_paths[0]
    path_nodes = [hop.get("from_id") for hop in first_alt] + [first_alt[-1].get("to_id")]
    assert "path_b_backup" in path_nodes
    assert "path_a_leak" not in path_nodes


def test_project_causal_impact_simulation(param_env):
    """Seam 3: 前瞻决策影响面模拟——顺着 causes/leads_to 拓扑模拟未来次生影响与置信度衰减，
    包含防成环死锁机制。
    """
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("start_action", "降低微服务请求超时阈值至50ms", "user_alice", t1),
            ("impact_hop1", "下游弱网请求大量超时重试", "user_alice", t2),
            ("impact_hop2", "网关队列打满导致雪崩", "user_alice", t3),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)

    engine.link_causality("start_action", "impact_hop1", relation="causes", confidence=0.8)
    engine.link_causality("impact_hop1", "impact_hop2", relation="leads_to", confidence=0.75)
    # 人为构造一个回环以测试防死循环机制
    engine.link_causality("impact_hop2", "start_action", relation="leads_to", confidence=0.5)

    res = engine.project_causal_impact(node_id="start_action", max_depth=5)
    assert res["start_node_id"] == "start_action"
    impacted = res.get("impacted_nodes", [])
    impacted_ids = [n["node_id"] for n in impacted]
    assert "impact_hop1" in impacted_ids
    assert "impact_hop2" in impacted_ids

    # 验证累积置信度衰减 (0.8 * 0.75 = 0.6)
    hop2_record = next(n for n in impacted if n["node_id"] == "impact_hop2")
    assert pytest.approx(hop2_record["cumulative_confidence"], 0.01) == 0.6
    assert hop2_record["depth"] == 2


def test_find_critical_causal_path(param_env):
    """Seam 4: 最强关键因果路径量化——在 DAG 中根据置信度乘积最大化寻找主导因果链。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
    t4 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("source_init", "系统配置更新", "user_alice", t1),
            ("weak_branch", "次要告警延迟触发", "user_alice", t2),
            ("strong_branch", "主库死锁超时触发", "user_alice", t3),
            ("incident_end", "订单交易失败率升高", "user_alice", t4),
        ],
    )
    user = _principal("user_alice")
    engine = CausalEngine(principal=user)

    # 弱路径: 0.5 * 0.4 = 0.20
    engine.link_causality("source_init", "weak_branch", relation="causes", confidence=0.5)
    engine.link_causality("weak_branch", "incident_end", relation="leads_to", confidence=0.4)

    # 强路径: 0.9 * 0.85 = 0.765
    engine.link_causality("source_init", "strong_branch", relation="causes", confidence=0.9)
    engine.link_causality("strong_branch", "incident_end", relation="leads_to", confidence=0.85)

    res = engine.find_critical_causal_path(target_id="incident_end")
    assert res["target_id"] == "incident_end"
    assert pytest.approx(res["path_confidence"], 0.01) == 0.765
    assert res["root_id"] == "source_init"

    hops = res.get("critical_path", [])
    assert len(hops) == 2
    assert hops[0]["from_id"] == "source_init"
    assert hops[0]["to_id"] == "strong_branch"
    assert hops[1]["from_id"] == "strong_branch"
    assert hops[1]["to_id"] == "incident_end"


def test_counterfactual_engine_acl_isolation(param_env):
    """Seam 5: 严格的行级 ACL 隔离防御——非本人/非本租户的记忆节点不可参与反事实推演与影响面模拟。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("bob_secret_cause", "Bob的内部操作", "user_bob", t1),
            ("bob_secret_effect", "Bob的系统变更", "user_bob", t2),
        ],
    )

    # Alice 试图推演 Bob 的节点
    alice = _principal("user_alice")
    engine = CausalEngine(principal=alice)

    # Alice 评估 Bob 的反事实推演，由于不可见直接返回不可达/无权限
    res = engine.evaluate_counterfactual(
        target_id="bob_secret_effect", intervened_node_id="bob_secret_cause"
    )
    assert res["found"] is False
    assert res["is_necessary"] is False

    # Alice 模拟 Bob 节点的影响面，直接返回空
    impact = engine.project_causal_impact(node_id="bob_secret_cause")
    assert impact["found"] is False
    assert len(impact.get("impacted_nodes", [])) == 0
