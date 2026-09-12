import pytest
from sqlmodel import Session, SQLModel, create_engine

try:
    from lantai.cognition.context import CognitiveContext, CognitiveContextBuilder
    from lantai.models.tables import CognitivePattern, CognitiveRole, FailureRecord, MemoryItem
except ImportError:
    CognitiveContextBuilder = None
    CognitiveContext = None
    MemoryItem = None
    CognitiveRole = None
    FailureRecord = None
    CognitivePattern = None


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_cognitive_context_structure(test_db):
    """CognitiveContext 必须包含 7 个标准切面。"""
    assert CognitiveContextBuilder is not None, "CognitiveContextBuilder is missing!"
    assert CognitiveContext is not None, "CognitiveContext is missing!"

    builder = CognitiveContextBuilder(test_db)
    ctx = builder.build(task="deploy lantai", top_k=10)

    assert hasattr(ctx, "facts")
    assert hasattr(ctx, "experience")
    assert hasattr(ctx, "beliefs")
    assert hasattr(ctx, "rules")
    assert hasattr(ctx, "principles")
    assert hasattr(ctx, "failures")
    assert hasattr(ctx, "conflicts")
    assert isinstance(ctx.facts, list)
    assert isinstance(ctx.rules, list)


def test_cognitive_context_segregation(test_db):
    """不同 CognitiveRole 的内容必须被正确分流到各自切面。"""
    assert MemoryItem is not None

    # 插入各角色的 MemoryItem
    test_db.add(
        MemoryItem(
            id="obs1", content="Python GIL limits CPU parallelism", role=CognitiveRole.OBSERVATION
        )
    )
    test_db.add(
        MemoryItem(
            id="exp1",
            content="Tried threading for CPU tasks, was slow",
            role=CognitiveRole.EXPERIENCE,
        )
    )
    test_db.add(
        MemoryItem(
            id="bel1",
            content="Threading is unsuitable for CPU-bound tasks",
            role=CognitiveRole.BELIEF,
        )
    )
    test_db.add(
        MemoryItem(
            id="rul1", content="Use multiprocessing for CPU-bound tasks", role=CognitiveRole.RULE
        )
    )
    test_db.add(
        MemoryItem(
            id="pri1",
            content="Match concurrency model to task type",
            role=CognitiveRole.PRINCIPLE,
            structure={"scope": {"domain": "python"}},
        )
    )
    test_db.commit()

    builder = CognitiveContextBuilder(test_db)
    ctx = builder.build(task="CPU-bound parallelism", top_k=20)

    # 确认各切面分流正确
    fact_ids = [m["id"] for m in ctx.facts]
    exp_ids = [m["id"] for m in ctx.experience]
    belief_ids = [m["id"] for m in ctx.beliefs]
    rule_ids = [m["id"] for m in ctx.rules]
    principle_ids = [m["id"] for m in ctx.principles]

    assert "obs1" in fact_ids
    assert "exp1" in exp_ids
    assert "bel1" in belief_ids
    assert "rul1" in rule_ids
    assert "pri1" in principle_ids


def test_cognitive_context_includes_failures(test_db):
    """Failures 切面必须包含 FailureRecord 条目。"""
    assert FailureRecord is not None

    test_db.add(
        FailureRecord(
            id="f1",
            task="deploy",
            action="4_workers",
            expected="stable",
            actual="crash",
            severity=0.8,
            recurrence_count=1,
            source_ids=[],
        )
    )
    test_db.commit()

    builder = CognitiveContextBuilder(test_db)
    ctx = builder.build(task="deploy lantai", top_k=10)

    assert len(ctx.failures) >= 1
    assert ctx.failures[0]["task"] == "deploy"


def test_cognitive_context_to_prompt(test_db):
    """CognitiveContext 能够生成供 Agent 使用的结构化 Markdown 提示。"""
    assert CognitiveContextBuilder is not None

    test_db.add(
        MemoryItem(id="r1", content="Don't share SQLite across threads", role=CognitiveRole.RULE)
    )
    test_db.commit()

    builder = CognitiveContextBuilder(test_db)
    ctx = builder.build(task="database setup", top_k=10)
    prompt = ctx.to_prompt()

    assert "## Governing Rules" in prompt
    assert "Don't share SQLite across threads" in prompt
    assert "## Relevant Facts" in prompt


def test_context_task_relevance_sorting(test_db):
    """task_relevance 排序：sqlite 相关 rule 应排在 UI rule 之前，即便置信度更低。"""
    assert MemoryItem is not None

    # rule_sqlite: 与 task 'sqlite database optimization' 高度相关，但 confidence 较低
    test_db.add(
        MemoryItem(
            id="rule_sqlite",
            content="always use SQLite WAL mode for database optimization",
            role=CognitiveRole.RULE,
            confidence=0.7,
        )
    )
    # rule_ui: 与 task 无关，但 confidence 更高
    test_db.add(
        MemoryItem(
            id="rule_ui",
            content="use dark mode UI theme",
            role=CognitiveRole.RULE,
            confidence=0.9,
        )
    )
    test_db.commit()

    builder = CognitiveContextBuilder(test_db)
    ctx = builder.build(task="sqlite database optimization", top_k=5)

    assert len(ctx.rules) >= 1, "rules 切面不应为空"
    assert ctx.rules[0]["id"] == "rule_sqlite", (
        f"期望 rule_sqlite 排在第一位（task 相关性更高），实际得到: {ctx.rules[0]['id']}"
    )
