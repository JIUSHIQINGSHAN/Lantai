"""
tests/test_runwu_engine_smoke.py
v0.39「润物」自适应认知装载引擎 RunwuEngine 冒烟测试（全不 mock）

遵循 AGENTS.md 测试纪律：
- 真实构造 SQLite 内存数据库；
- 真实调用 RunwuEngine 核心计算与萃取；
- 绝不 mock 被测函数内部逻辑。
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.ids import new_id
from lantai.models.tables import CognitiveRole, ConflictEvent, FailureRecord, MemoryItem
from lantai.runtime.runwu import RunwuEngine, RunwuSummary


@pytest.fixture(name="db_session")
def db_session_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seam1_runwu_extracts_rules_and_failures(db_session: Session):
    """Seam 1: 基础认知萃取冒烟——正确提取规则、失败教训并格式化为紧凑字符串。"""
    # 插入有效规则
    r1 = MemoryItem(
        id=new_id("mem"),
        content="生产环境修改数据库前必须先做快照备份",
        role=CognitiveRole.RULE,
        status="active",
        confidence=0.95,
        user_id="user_alpha",
        tenant_id="tenant_1",
    )
    # 插入已知失败教训
    f1 = FailureRecord(
        id=new_id("fl"),
        task="直接修改配置文件重启服务",
        action="modify_config",
        expected="配置生效且服务正常",
        actual="配置文件格式错误导致服务崩溃",
        lesson="必须先使用校验工具验证配置语法正确后再重启",
        severity=0.8,
    )
    db_session.add(r1)
    db_session.add(f1)
    db_session.commit()

    engine = RunwuEngine(db=db_session)
    summary: RunwuSummary = engine.extract_summary(
        task="准备修改数据库配置并重启",
        user_id="user_alpha",
        tenant_id="tenant_1",
        max_rules=2,
        max_failures=1,
    )

    assert isinstance(summary, RunwuSummary)
    assert len(summary.rules) >= 1
    assert "快照备份" in summary.rules[0]
    assert len(summary.failures) == 1
    assert "校验工具" in summary.failures[0]

    compact_text = engine.format_compact(summary)
    assert "相关规则:" in compact_text
    assert "已知教训:" in compact_text
    # 验证樊篱包裹标签在场
    assert "<memory_data" in compact_text
    assert "</memory_data>" in compact_text


def test_seam2_runwu_tenant_isolation(db_session: Session):
    """Seam 2: 租户隔离校验——严格跨租户零泄漏。"""
    # Tenant A 的秘密规则
    r_a = MemoryItem(
        id=new_id("mem"),
        content="租户A的机密操作守则：禁止向公网暴露端口",
        role=CognitiveRole.RULE,
        status="active",
        confidence=0.9,
        user_id="user_a",
        tenant_id="tenant_A",
    )
    # Tenant B 的公开规则
    r_b = MemoryItem(
        id=new_id("mem"),
        content="租户B的通用开发规范：代码统一使用UTF-8编码",
        role=CognitiveRole.RULE,
        status="active",
        confidence=0.85,
        user_id="user_b",
        tenant_id="tenant_B",
    )
    db_session.add(r_a)
    db_session.add(r_b)
    db_session.commit()

    engine = RunwuEngine(db=db_session)

    # 租户 B 的 Agent 执行任务
    summary_b = engine.extract_summary(
        task="检查开发与部署规范",
        user_id="user_b",
        tenant_id="tenant_B",
    )

    compact_b = engine.format_compact(summary_b)
    assert "租户B" in compact_b
    assert "租户A" not in compact_b
    assert "机密操作守则" not in compact_b


def test_seam3_runwu_budget_and_truncation(db_session: Session):
    """Seam 3: 字符预算超限截断——确保不超过指定预算上限。"""
    # 插入长正文规则
    very_long_rule = "这是一个极度冗长且繁琐的规则描述：" + ("遵守合规要求非常重要！" * 30)
    r_long = MemoryItem(
        id=new_id("mem"),
        content=very_long_rule,
        role=CognitiveRole.RULE,
        status="active",
        confidence=0.9,
        user_id="user_alpha",
        tenant_id="tenant_1",
    )
    db_session.add(r_long)
    db_session.commit()

    engine = RunwuEngine(db=db_session)
    summary = engine.extract_summary(
        task="执行合规检查",
        user_id="user_alpha",
        tenant_id="tenant_1",
        char_budget=120,
    )

    compact = engine.format_compact(summary)
    # 严格保证字符控制在预算浮动安全范围内
    assert len(compact) <= 180
    assert summary.char_count <= 180


def test_seam4_runwu_integrates_probes(db_session: Session):
    """Seam 4: 探颐主动求证探针联动萃取——当前租户存在未决冲突或模糊事实时带出追问。"""
    # 插入未决冲突与记忆
    m1 = MemoryItem(
        id=new_id("mem"),
        content="张三常驻办公地点是上海总部",
        role=CognitiveRole.OBSERVATION,
        status="active",
        confidence=0.55,
        user_id="user_alpha",
        tenant_id="tenant_1",
    )
    conf = ConflictEvent(
        id=new_id("conf"),
        tenant_id="tenant_1",
        user_id="user_alpha",
        memory_id=m1.id,
        incoming_content="张三常驻办公地点已变更为北京分部",
        status="open",
    )
    db_session.add(m1)
    db_session.add(conf)
    db_session.commit()

    engine = RunwuEngine(db=db_session)
    summary = engine.extract_summary(
        task="确认张三常驻地点并处理工单",
        user_id="user_alpha",
        tenant_id="tenant_1",
        max_probes=1,
    )

    assert len(summary.probes) >= 1
    compact = engine.format_compact(summary)
    assert "待求证:" in compact or "待确认:" in compact
    assert "张三" in compact
