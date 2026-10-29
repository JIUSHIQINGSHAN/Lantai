"""参商·核心深层冲突检测与信念一致性仲裁引擎冒烟测试 (v0.41 Ticket 01)

基于真实 in-memory SQLite，遵循 Matt Pocock TDD 纪律，不使用 mock。
覆盖公共接口 Seams：
1. detect_pairwise_contradiction: 两两直接相反主张与时间轴错位矛盾判定与严重度量化；
2. audit_consistency (多跳隐性推导): 沿 MemoryEdge causes/leads_to 推演次生结论并发现跨节点相悖；
3. audit_consistency (实体/范围一致性扫描): 全局或指定实体的全景一致性审计报告与指标统计；
4. arbitrate_deep_conflict: 基于更漏事件轴、证据独立性与置信度加权的深层多方仲裁直断；
5. 租户行级 ACL 严格隔离防护：未授权租户/用户的数据不可探测、不可穿透推演。
"""

from datetime import UTC, datetime

import pytest

from lantai.cognition.canshang import (
    CanshangEngine,
    ConsistencyAuditReport,
    ConsistencyViolation,
    DeepArbitrationResult,
)
from lantai.core.auth import Principal
from lantai.models.tables import MemoryEdge, MemoryItem


def _principal(user_id: str, *, admin: bool = False, lanes=None) -> Principal:
    return Principal(
        user_id=user_id,
        allowed_lanes=lanes,
        role="admin" if admin else "user",
    )


def _seed_memories(
    param_env,
    items: list[tuple[str, str, str, datetime | None, dict | None]],
):
    """(id, content, user_id, event_time, structure) 批量写入真实测试数据库"""
    session_factory, _ = param_env
    with session_factory() as s:
        for mid, content, uid, et, st in items:
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
                    structure=st or {},
                    confidence=0.85,
                )
            )
        s.commit()


def _seed_edge(param_env, source_id: str, target_id: str, relation: str, confidence: float = 0.9):
    session_factory, _ = param_env
    with session_factory() as s:
        s.add(
            MemoryEdge(
                id=f"e-{source_id}-{target_id}",
                source_memory_id=source_id,
                target_memory_id=target_id,
                relation=relation,
                confidence=confidence,
            )
        )
        s.commit()


def test_detect_pairwise_contradiction_direct(param_env):
    """Seam 1: 两两直接主张矛盾检测——同一实体在同一维度的主张完全相反时，精准输出高严重度矛盾。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            (
                "mem_srv_on",
                "生产服务器已全面启用 TLS 1.3 强加密传输",
                "user_alice",
                t1,
                {"entity": "prod_server", "feature": "encryption", "value": "tls13_enabled"},
            ),
            (
                "mem_srv_off",
                "生产服务器目前完全关闭 TLS 加密使用明文通信",
                "user_alice",
                t2,
                {"entity": "prod_server", "feature": "encryption", "value": "tls_disabled"},
            ),
        ],
    )

    user = _principal("user_alice")
    engine = CanshangEngine(principal=user)

    session_factory, _ = param_env
    with session_factory() as s:
        item_a = s.get(MemoryItem, "mem_srv_on")
        item_b = s.get(MemoryItem, "mem_srv_off")

        violation = engine.detect_pairwise_contradiction(item_a, item_b, session=s)

    assert violation is not None
    assert isinstance(violation, ConsistencyViolation)
    assert set(violation.conflicting_memory_ids) == {"mem_srv_on", "mem_srv_off"}
    assert violation.violation_type in ("direct_contradiction", "attribute_conflict")
    assert violation.severity in ("high", "medium")
    assert "encryption" in violation.description or "矛盾" in violation.description


def test_audit_consistency_multihop_causal(param_env):
    """Seam 2: 多跳因果链隐性矛盾——A 经 MemoryEdge causes 衍生状态 C，而 B 直接主张 not C。
    扫描审计时能通过因果拓扑发现多跳相悖。
    """
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            (
                "step_deploy",
                "执行命令升级生产数据库至高可用分片集群模式",
                "user_alice",
                t1,
                {"action": "cluster_mode"},
            ),
            (
                "step_cluster_state",
                "系统处于多主集群高可用状态且主从复制活跃",
                "user_alice",
                t2,
                {"state": "multi_master_cluster"},
            ),
            (
                "claim_standalone",
                "当前线上仅有单机独立数据库运行且无从节点",
                "user_alice",
                t3,
                {"state": "single_node_standalone"},
            ),
        ],
    )

    _seed_edge(param_env, "step_deploy", "step_cluster_state", "causes", confidence=0.95)

    user = _principal("user_alice")
    engine = CanshangEngine(principal=user)

    report = engine.audit_consistency(
        memory_ids=["step_deploy", "step_cluster_state", "claim_standalone"]
    )
    assert isinstance(report, ConsistencyAuditReport)
    assert report.total_scanned == 3
    assert report.violation_count >= 1

    # 验证捕获到了多跳/间接矛盾
    viol = next(
        (v for v in report.violations if "claim_standalone" in v.conflicting_memory_ids), None
    )
    assert viol is not None
    assert (
        "step_cluster_state" in viol.conflicting_memory_ids
        or "step_deploy" in viol.conflicting_memory_ids
    )


def test_audit_consistency_by_entity(param_env):
    """Seam 3: 基于指定实体的全局一致性矩阵审计——输出规范的一致性审计报告。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            (
                "ent_alice_role_pm",
                "张三目前在兰台项目担任产品经理",
                "user_alice",
                t1,
                {"entity": "张三", "role": "PM"},
            ),
            (
                "ent_alice_role_dev",
                "张三全职担任兰台底层引擎研发工程师，不参与任何产品设计",
                "user_alice",
                t2,
                {"entity": "张三", "role": "Dev"},
            ),
        ],
    )

    user = _principal("user_alice")
    engine = CanshangEngine(principal=user)

    report = engine.audit_consistency(entity_name="张三")
    assert isinstance(report, ConsistencyAuditReport)
    assert report.total_scanned >= 2
    assert report.violation_count >= 1
    assert any("张三" in v.description for v in report.violations)


def test_arbitrate_deep_conflict(param_env):
    """Seam 4: 深层多方冲突仲裁直断——结合更漏事件轴 event_time 与证据质量，
    判定主导记忆（dominant_memory_id），并给出详尽的决策理由与直断叙述。
    """
    # t_early vs t_late，晚近事实在事实更正中主导
    t_early = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    t_late = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            (
                "old_policy",
                "办公网默认开放 22 端口供全员远程 SSH 直连",
                "user_alice",
                t_early,
                {"policy": "ssh_open"},
            ),
            (
                "new_policy",
                "安全委员会决议：全面废除 22 直连，必须经零信任网关二次认证进入",
                "user_alice",
                t_late,
                {"policy": "ssh_closed_ztna_required"},
            ),
        ],
    )

    user = _principal("user_alice")
    engine = CanshangEngine(principal=user)

    result = engine.arbitrate_deep_conflict(["old_policy", "new_policy"])
    assert isinstance(result, DeepArbitrationResult)
    assert result.dominant_memory_id == "new_policy"
    assert "old_policy" in result.subordinate_memory_ids
    assert result.resolution_strategy in ("supersede", "deprecate_subordinate", "win_late")
    assert "决议" in result.narrative or "时效" in result.narrative or "胜出" in result.narrative
    assert "scores" in result.decision_trace


def test_canshang_acl_isolation(param_env):
    """Seam 5: 严格的行级 ACL 隔离防御——非本用户数据不可被扫描或推演，宁 miss 不脏写。"""
    t1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)

    _seed_memories(
        param_env,
        [
            ("alice_secret", "Alice 的机密偏好设置 A", "user_alice", t1, {"entity": "target_e"}),
            ("bob_secret", "Bob 的相反机密偏好设置 B", "user_bob", t1, {"entity": "target_e"}),
        ],
    )

    # 以 Alice 身份调用
    alice_user = _principal("user_alice")
    engine_alice = CanshangEngine(principal=alice_user)

    # 尝试指定 Bob 的私有条目进行仲裁或审计
    report = engine_alice.audit_consistency(memory_ids=["alice_secret", "bob_secret"])
    # bob_secret 应该被 ACL 拦截过滤，只扫描到 alice_secret 1 条，无法 cross-tenant 泄漏矛盾
    assert report.total_scanned == 1
    assert report.violation_count == 0

    # 尝试直接对 Bob 的条目发起仲裁
    arb = engine_alice.arbitrate_deep_conflict(["bob_secret"])
    assert arb.dominant_memory_id is None
    assert "无权访问" in arb.narrative or "未找到" in arb.narrative
