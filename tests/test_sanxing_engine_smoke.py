"""端到端不 mock 冒烟测试：三省认知自省与健康自愈闭环引擎（Ticket 01）。

覆盖 4 个独立接缝：
1. 接缝 1：纯函数缺陷自愈方案生成（generate_remedies: auto_apply=False 预演）；
2. 接缝 2：真实 SQLite DB 单项自愈落库流转（auto_apply=True 就地修复）；
3. 接缝 3：批量健康自省与健康分动态自愈变迁；
4. 接缝 4：行级租户属主安全隔离校验（宁漏不越权）与樊篱防注入围栏包裹。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.sanxing import SanxingEngine, SanxingRemedyResult
from lantai.core.auth import Principal
from lantai.models.tables import ConflictEvent, MemoryItem


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seam1_pure_remedy_generation(db_session: Session):
    """Seam 1: 纯函数缺陷自愈方案生成（预演不落库）。"""
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

    # 1. 制造一条过期记忆
    expired_mem = MemoryItem(
        id="mem_exp_01",
        content="上季度临时活动配置",
        status="active",
        valid_to=now - timedelta(days=1),
        decay_class="exponential",
    )
    # 2. 制造一条被替代记忆
    superseded_mem = MemoryItem(
        id="mem_sup_01",
        content="老版认证接口规范",
        status="active",
        structure={"superseded_by": "mem_sup_new"},
    )
    # 3. 制造一条低帮助碎片
    unhelpful_mem = MemoryItem(
        id="mem_unh_01",
        content="高频调用但无法解答的残缺问答",
        status="active",
        use_count=6,
        helpful_count=0,
    )
    db_session.add_all([expired_mem, superseded_mem, unhelpful_mem])
    db_session.commit()

    engine = SanxingEngine(session=db_session)
    res: SanxingRemedyResult = engine.generate_remedies(auto_apply=False, now=now)

    assert res.total_defects == 3
    assert res.applied_count == 0  # 预演模式，未写库
    actions = {r.recommended_action for r in res.remedies}
    assert "archive_expire" in actions
    assert "supersede_retract" in actions
    assert "demote_deprecate" in actions

    # 验证 DB 内状态未变
    assert db_session.get(MemoryItem, "mem_exp_01").status == "active"


def test_seam2_single_item_remediation_apply(db_session: Session):
    """Seam 2: 真实 SQLite DB 自愈就地执行落库。"""
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

    mem_to_expire = MemoryItem(
        id="mem_to_expire",
        content="已过期服务鉴权凭证",
        status="active",
        valid_to=now - timedelta(hours=2),
        decay_class="linear",
    )
    db_session.add(mem_to_expire)
    db_session.commit()

    engine = SanxingEngine(session=db_session)
    res = engine.generate_remedies(auto_apply=True, now=now)

    assert res.total_defects == 1
    assert res.applied_count == 1

    # 验证落库持久化：状态已流转为 archived
    refreshed = db_session.get(MemoryItem, "mem_to_expire")
    assert refreshed.status == "archived"


def test_seam3_batch_health_score_transition(db_session: Session):
    """Seam 3: 批量健康自省与健康分动态变迁。"""
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

    mem_stale = MemoryItem(
        id="mem_stale_01",
        content="废弃 API 路由",
        status="active",
        structure={"superseded_by": "mem_new_01"},
    )
    mem_low_help = MemoryItem(
        id="mem_low_01",
        content="无意义社交闲聊记录",
        status="active",
        use_count=7,
        helpful_count=0,
        importance=0.8,
    )
    db_session.add_all([mem_stale, mem_low_help])
    db_session.commit()

    engine = SanxingEngine(session=db_session)
    res = engine.generate_remedies(auto_apply=True, now=now)

    # 修复前 90.0 分（扣 10 分），修复后全部解决重回 100.0 分
    assert res.health_score_before == 90.0
    assert res.health_score_after == 100.0
    assert res.applied_count == 2
    # 验证 low_help 重要度降权
    assert db_session.get(MemoryItem, "mem_low_01").importance <= 0.1


def test_seam4_acl_isolation_and_fencing(db_session: Session):
    """Seam 4: 行级租户安全隔离校验与樊篱数据围栏包裹。"""
    now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)

    mem_tenant_b = MemoryItem(
        id="mem_b_defect",
        content="租户B保密系统过期密钥",
        status="active",
        valid_to=now - timedelta(days=5),
        user_id="bob",
        tenant_id="tenant_b",
    )
    db_session.add(mem_tenant_b)
    db_session.commit()

    # 租户 A 执行自省，不应扫描到租户 B 的缺陷
    principal_a = Principal(user_id="alice", tenant_id="tenant_a", role="user")
    engine_a = SanxingEngine(session=db_session, principal=principal_a)
    res_a = engine_a.generate_remedies(auto_apply=True, now=now)
    assert res_a.total_defects == 0

    # 租户 B 执行自省，校验樊篱防注入围栏
    principal_b = Principal(user_id="bob", tenant_id="tenant_b", role="user")
    engine_b = SanxingEngine(session=db_session, principal=principal_b)
    res_b = engine_b.generate_remedies(auto_apply=True, now=now)
    assert res_b.total_defects == 1
    remedy = res_b.remedies[0]
    assert "<memory_data" in remedy.fenced_content
    assert "租户B保密系统过期密钥" in remedy.fenced_content
