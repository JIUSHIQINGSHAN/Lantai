"""端到端不 mock 冒烟测试：磨勘核心交叉勘验与可信度自证引擎 (Ticket 01)。

遵循 Matt Pocock TDD 纪律，覆盖 4 个独立接缝（Seams）：
1. 接缝 1 (纯函数可信度自证打分算法边界)：MokanEngine.compute_trust_score 边界与增益衰减；
2. 接缝 2 (真实 SQLite 一致性多源证据交叉勘验)：多源证据交叉比对、通过认证与可信度自证提升；
3. 接缝 3 (真实 SQLite 矛盾证据识别与安全降权)：对立与矛盾事实拓扑自动标记 disputed 与惩罚降权；
4. 接缝 4 (行级 ACL 隔离与樊篱防注入防御)：跨租户勘验严格阻断（宁 miss 不越权）与 wrap_as_data 防提示词注入。
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.mokan import (
    MokanAuditResult,
    MokanEngine,
    MokanVerificationItem,
)
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem


@pytest.fixture(name="db_session")
def fixture_db_session():
    """提供真实 SQLite 内存数据库 Session，不 mock 数据库层。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_mokan_trust_score_computation():
    """接缝 1：纯函数可信度自证打分边界（无 DB 副作用）。"""
    engine = MokanEngine()

    # 1. 单源且无额外证据：基础分
    base = 0.6
    score_single = engine.compute_trust_score(base_confidence=base, evidence_count=1)
    assert score_single == base

    # 2. 多源独立证据交叉印证（2个与3个来源）：可信度递增
    score_multi2 = engine.compute_trust_score(base_confidence=base, evidence_count=2)
    score_multi3 = engine.compute_trust_score(base_confidence=base, evidence_count=3)
    assert score_multi2 > base
    assert score_multi3 > score_multi2
    assert score_multi3 <= 1.0

    # 3. 存在矛盾证据：实施可信度惩罚降权
    score_disputed = engine.compute_trust_score(
        base_confidence=base, evidence_count=2, contradiction_penalty=0.4
    )
    assert score_disputed < score_multi2
    assert score_disputed < base

    # 4. 边界截断保护
    assert engine.compute_trust_score(base_confidence=-0.5, evidence_count=0) == 0.0
    assert engine.compute_trust_score(base_confidence=1.5, evidence_count=10) == 1.0


def test_mokan_cross_verify_consistent_sources(db_session: Session):
    """接缝 2：真实 SQLite 数据层多源一致性证据交叉勘验通过。"""
    principal = Principal(user_id="analyst", tenant_id="tenant_mokan")
    engine = MokanEngine(session=db_session, principal=principal)

    now = utcnow()
    # 构造两条相互支撑的多源事实
    m1 = MemoryItem(
        id="mem_mokan_1",
        content="兰台长期记忆系统使用 SQLite FTS5 与向量双轨混合检索",
        confidence=0.7,
        status="active",
        user_id="analyst",
        tenant_id="tenant_mokan",
        created_at=now,
    )
    m2 = MemoryItem(
        id="mem_mokan_2",
        content="兰台的检索层支持 FTS5 全文检索与向量嵌入召回",
        confidence=0.8,
        status="active",
        user_id="analyst",
        tenant_id="tenant_mokan",
        created_at=now,
    )
    # 关联 supports 证据拓扑边
    edge = MemoryEdge(
        id="edge_mokan_supports",
        source_memory_id="mem_mokan_1",
        target_memory_id="mem_mokan_2",
        relation="supports",
        confidence=0.9,
        user_id="analyst",
        tenant_id="tenant_mokan",
    )
    db_session.add(m1)
    db_session.add(m2)
    db_session.add(edge)
    db_session.commit()

    # 执行交叉勘验
    verified_items: list[MokanVerificationItem] = engine.cross_verify_memories(
        memory_ids=["mem_mokan_1", "mem_mokan_2"], session=db_session
    )

    assert len(verified_items) == 2
    item1 = next(it for it in verified_items if it.memory_id == "mem_mokan_1")
    assert item1.verification_status == "verified"
    assert "mem_mokan_2" in item1.cross_sources
    assert item1.evidence_count >= 2
    assert item1.trust_score > 0.7  # 获得交叉验证增益
    assert "<memory_data" in item1.fenced_content


def test_mokan_cross_verify_contradiction_detection(db_session: Session):
    """接缝 3：真实 SQLite 矛盾证据识别、标记 disputed 与安全降权。"""
    principal = Principal(user_id="analyst", tenant_id="tenant_mokan")
    engine = MokanEngine(session=db_session, principal=principal)

    now = utcnow()
    # 构造两条相互冲突的事实
    m_main = MemoryItem(
        id="mem_conflict_main",
        content="生产环境数据库连接池上限配置为 50",
        confidence=0.85,
        status="active",
        user_id="analyst",
        tenant_id="tenant_mokan",
        created_at=now,
    )
    m_contra = MemoryItem(
        id="mem_conflict_contra",
        content="生产环境数据库连接池上限配置为 10",
        confidence=0.8,
        status="active",
        user_id="analyst",
        tenant_id="tenant_mokan",
        created_at=now,
    )
    # 建立 contradicts 矛盾边
    edge_contra = MemoryEdge(
        id="edge_mokan_contra",
        source_memory_id="mem_conflict_main",
        target_memory_id="mem_conflict_contra",
        relation="contradicts",
        confidence=0.95,
        user_id="analyst",
        tenant_id="tenant_mokan",
    )
    db_session.add(m_main)
    db_session.add(m_contra)
    db_session.add(edge_contra)
    db_session.commit()

    # 审计矛盾
    audit_res: MokanAuditResult = engine.audit_contradictions(
        query="数据库连接池", session=db_session
    )

    assert audit_res.disputed_count >= 2
    disputed_items = [it for it in audit_res.items if it.verification_status == "disputed"]
    assert len(disputed_items) >= 2
    disputed_ids = [it.memory_id for it in disputed_items]
    assert "mem_conflict_main" in disputed_ids
    assert "mem_conflict_contra" in disputed_ids

    # 矛盾项可信度必然被惩罚性降权
    main_item = next(it for it in disputed_items if it.memory_id == "mem_conflict_main")
    assert main_item.trust_score < main_item.confidence_score
    assert "mem_conflict_contra" in main_item.contradiction_ids


def test_mokan_tenant_isolation(db_session: Session):
    """接缝 4：多租户行级数据隔离与樊篱防御（宁 miss 不脏写）。"""
    # 租户 A 用户
    principal_a = Principal(user_id="alice", tenant_id="tenant_A")
    engine_a = MokanEngine(session=db_session, principal=principal_a)

    now = utcnow()
    # 属于租户 B 的高密记忆与边
    mem_b = MemoryItem(
        id="mem_tenant_b_secret",
        content="租户B专用高管身份认证凭据与内部安全机密",
        confidence=0.9,
        status="active",
        user_id="bob",
        tenant_id="tenant_B",
        created_at=now,
    )
    # 属于租户 A 的正常记忆
    mem_a = MemoryItem(
        id="mem_tenant_a_normal",
        content="租户A常规开发团队周报信息",
        confidence=0.75,
        status="active",
        user_id="alice",
        tenant_id="tenant_A",
        created_at=now,
    )
    db_session.add(mem_b)
    db_session.add(mem_a)
    db_session.commit()

    # 租户 A 尝试交叉勘验租户 B 的记忆：隔离阻断，绝不泄露
    verified_items = engine_a.cross_verify_memories(
        memory_ids=["mem_tenant_b_secret", "mem_tenant_a_normal"], session=db_session
    )
    res_ids = [it.memory_id for it in verified_items]
    assert "mem_tenant_b_secret" not in res_ids
    assert "mem_tenant_a_normal" in res_ids

    # 审计矛盾时同样严守租户边界
    audit_res = engine_a.audit_contradictions(query="机密", session=db_session)
    for it in audit_res.items:
        assert it.tenant_id != "tenant_B"
