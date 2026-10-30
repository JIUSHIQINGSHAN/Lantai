"""端到端不 mock 冒烟测试：考功核心价值演化与功过评定引擎 KaogongEngine（Ticket 01）。

覆盖 4 个独立接缝：
1. 接缝 1：纯函数多因子打分与动作裁决（evaluate_item: promote_longterm / demote_deprecate / keep_neutral）；
2. 接缝 2：真实 SQLite DB 单条记忆考评与生命周期落库流转；
3. 接缝 3：批量考功巡检与结构化审计快照生成；
4. 接缝 4：行级租户属主安全隔离校验（宁漏不越权）与樊篱防注入围栏包裹。
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.kaogong import KaogongEngine, KaogongResult
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem


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


def test_seam1_pure_func_evaluation():
    """Seam 1: 纯函数多因子评分与动作裁决（不 mock 内部逻辑）。"""
    engine = KaogongEngine()

    # 1. 样本不足：use_count < 3，保持原状
    item_insufficient = MemoryItem(
        id="mem_01",
        content="临时缓存配置项",
        use_count=2,
        helpful_count=2,
        importance=0.5,
        tier="working",
    )
    res1 = engine.evaluate_item(item_insufficient)
    assert res1.action == "keep_neutral"
    assert res1.new_tier == "working"
    assert "样本不足" in res1.reason

    # 2. 上考：高频高采纳 (4/5 = 80%) -> 晋升长期，免疫衰减，提升重要度
    item_promoted = MemoryItem(
        id="mem_02",
        content="Docker 容器启动必须挂载只读根目录的安全铁律",
        use_count=5,
        helpful_count=4,
        importance=0.7,
        tier="working",
        decay_class="exponential",
    )
    res2 = engine.evaluate_item(item_promoted)
    assert res2.action == "promote_longterm"
    assert res2.new_tier == "longterm"
    assert res2.score >= 0.75
    assert res2.score_factors["helpful_score"] >= 0.7
    assert res2.new_importance > 0.7
    assert "考功上考" in res2.reason
    assert "Docker 容器启动" in res2.fenced_content

    # 3. 下考：高频低效 (1/5 = 20%) -> 降权至 0.1
    item_demoted = MemoryItem(
        id="mem_03",
        content="已废弃的旧版本临时调试指令",
        use_count=5,
        helpful_count=1,
        importance=0.8,
        tier="working",
    )
    res3 = engine.evaluate_item(item_demoted)
    assert res3.action == "demote_deprecate"
    assert res3.new_importance <= 0.2
    assert "考功下考" in res3.reason


def test_seam2_single_memory_evaluation_db(db_session: Session):
    """Seam 2: 真实 SQLite DB 单条记忆考评落库与状态流转。"""
    mem = MemoryItem(
        id="mem_db_01",
        content="Python 项目 poetry.lock 依赖锁定规范",
        use_count=10,
        helpful_count=9,
        importance=0.6,
        tier="working",
        decay_class="linear",
        user_id="alice",
        tenant_id="tenant_a",
        status="active",
    )
    db_session.add(mem)
    db_session.commit()

    engine = KaogongEngine(session=db_session)
    res = engine.evaluate_memory_id("mem_db_01")

    assert res.memory_id == "mem_db_01"
    assert res.action == "promote_longterm"
    assert res.new_tier == "longterm"

    # 验证落库持久化结果
    refreshed = db_session.get(MemoryItem, "mem_db_01")
    assert refreshed is not None
    assert refreshed.tier == "longterm"
    assert refreshed.decay_class == "semantic"
    assert refreshed.importance >= 0.7


def test_seam3_batch_evaluation_and_report(db_session: Session):
    """Seam 3: 批量考功巡检与审计报告快照。"""
    # 插入 3 条不同状态记忆
    mem1 = MemoryItem(
        id="mem_b_01",
        content="关键设计模式经验",
        use_count=6,
        helpful_count=5,
        importance=0.5,
        status="active",
    )
    mem2 = MemoryItem(
        id="mem_b_02",
        content="误导性错误配置",
        use_count=6,
        helpful_count=0,
        importance=0.5,
        status="active",
    )
    mem3 = MemoryItem(
        id="mem_b_03",
        content="全新知识点",
        use_count=1,
        helpful_count=1,
        importance=0.5,
        status="active",
    )
    db_session.add_all([mem1, mem2, mem3])
    db_session.commit()

    engine = KaogongEngine(session=db_session)
    report = engine.run_cycle()

    assert report["evaluated"] >= 3
    assert report["promoted_longterm"] >= 1
    assert report["demoted"] >= 1
    assert report["kept"] >= 1
    assert len(report["details"]) >= 2

    # 验证获取报告快照
    snap = engine.get_report()
    assert snap["evaluated"] == report["evaluated"]


def test_seam4_acl_isolation_and_fencing(db_session: Session):
    """Seam 4: 行级租户安全隔离（宁漏不越权）与樊篱防注入围栏。"""
    mem_tenant_b = MemoryItem(
        id="mem_secret_b",
        content="租户B专有保密架构",
        use_count=5,
        helpful_count=5,
        importance=0.5,
        user_id="bob",
        tenant_id="tenant_b",
        status="active",
    )
    db_session.add(mem_tenant_b)
    db_session.commit()

    # 租户 A 的 Principal 试图越权操作租户 B 的记忆
    principal_a = Principal(user_id="alice", tenant_id="tenant_a", role="user")
    engine_a = KaogongEngine(session=db_session, principal=principal_a)

    with pytest.raises(PermissionError) as exc_info:
        engine_a.evaluate_memory_id("mem_secret_b")
    assert "行级租户" in str(exc_info.value)

    # 正常同租户操作，校验樊篱防注入安全包裹
    principal_b = Principal(user_id="bob", tenant_id="tenant_b", role="user")
    engine_b = KaogongEngine(session=db_session, principal=principal_b)
    res_b = engine_b.evaluate_memory_id("mem_secret_b")
    assert res_b.action == "promote_longterm"
    assert "<memory_data" in res_b.fenced_content
    assert "租户B专有保密架构" in res_b.fenced_content
