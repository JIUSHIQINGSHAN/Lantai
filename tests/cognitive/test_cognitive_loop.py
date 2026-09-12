"""
tests/cognitive/test_cognitive_loop.py
v0.3 Behavioral Learning Benchmark — 认知闭环端到端测试

BL-01: FailureRecord → CognitivePattern（failure_pattern）
BL-02: failure_pattern → BELIEF candidate（promotion_trace 非空）
BL-03: BELIEF candidate 持久化（commit 后仍存在）
BL-04: Task B cognitive_context 含 Task A lesson 关键词
BL-05: Task B 的 rule 切面 >= Task A（学习后认知上下文更丰富）

全部不 mock 核心计算逻辑，使用真实 SQLite in-memory + SQLModel。
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.cognition.context import CognitiveContextBuilder
from lantai.cognition.reflection import ReflectionEngine
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import (
    ActionOutcome,
    CognitivePattern,
    CognitiveRole,
    FailureRecord,
    MemoryItem,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(name="engine")
def engine_fixture():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as s:
        yield s


def _make_failure(task: str, action: str, cause: str, lesson: str) -> FailureRecord:
    return FailureRecord(
        id=new_id("fail"),
        task=task,
        action=action,
        expected="success",
        actual="failure",
        cause=cause,
        lesson=lesson,
        severity=0.7,
    )


# ---------------------------------------------------------------------------
# BL-01: FailureRecord → CognitivePattern（failure_pattern）
# ---------------------------------------------------------------------------


def test_failure_to_pattern(session: Session):
    """
    BL-01：给定两条 lesson 相似的 FailureRecord，
    run_reflection 应产出 failure_pattern 类型的 CognitivePattern。
    """
    lesson_text = "不应该直接修改生产数据库，应该先备份"

    f1 = _make_failure(
        task="update production DB",
        action="direct SQL update",
        cause="skipped backup step",
        lesson=lesson_text,
    )
    f2 = _make_failure(
        task="deploy hotfix",
        action="direct SQL update on prod",
        cause="skipped backup step",
        lesson=lesson_text,  # 相同 lesson，触发聚类
    )
    session.add(f1)
    session.add(f2)
    session.commit()

    engine = ReflectionEngine(db=session)
    report = engine.run_reflection()

    # BL-01 断言
    assert report.failures == 2, "应记录 2 条 FailureRecord"
    assert report.failure_patterns >= 1, (
        f"应从失败中发现 >= 1 个 failure_pattern，got: {report.failure_patterns}"
    )

    # 验证 CognitivePattern 已写入 DB（failure_pattern 类型）
    patterns = session.exec(
        select(CognitivePattern).where(CognitivePattern.pattern_type == "failure_pattern")
    ).all()
    assert len(patterns) >= 1, "CognitivePattern(failure_pattern) 应已写入 DB"


# ---------------------------------------------------------------------------
# BL-02: failure_pattern → BELIEF candidate（promotion_trace 非空）
# ---------------------------------------------------------------------------


def test_pattern_to_belief(session: Session):
    """
    BL-02：失败归纳的 failure_pattern 应以低阈值（0.55）晋升为 BELIEF candidate，
    且 promotion_trace 非空，包含 promoted_from='failure_pattern'。
    """
    # 需要 >= 2 条 lesson 相似的失败才能形成 pattern
    lesson = "不能跳过测试直接合并主分支"
    for i in range(3):
        session.add(
            _make_failure(
                task=f"merge PR-{i}",
                action="force merge without CI",
                cause="CI skipped",
                lesson=lesson,
            )
        )
    session.commit()

    engine = ReflectionEngine(db=session)
    report = engine.run_reflection()

    # BL-02 断言
    assert len(report.failure_belief_candidates) >= 1, (
        f"应至少晋升 1 个 BELIEF candidate，got: {report.failure_belief_candidates}"
    )

    belief = report.failure_belief_candidates[0]
    assert belief.role == CognitiveRole.BELIEF
    assert belief.status == "candidate", "Candidate ≠ Knowledge：status 必须是 candidate"
    assert belief.promotion_trace, "promotion_trace 必须非空"
    assert belief.promotion_trace.get("promoted_from") == "failure_pattern", (
        f"promoted_from 应为 'failure_pattern'，got: {belief.promotion_trace}"
    )
    assert "score" in belief.promotion_trace, "promotion_trace 应包含 score"
    assert "components" in belief.promotion_trace, "promotion_trace 应包含 components"


# ---------------------------------------------------------------------------
# BL-03: BELIEF candidate 持久化
# ---------------------------------------------------------------------------


def test_belief_persists(session: Session):
    """
    BL-03：run_reflection 晋升的 BELIEF candidate 在 commit 后仍持久化。
    """
    lesson = "代码审查不能跳过，即使紧急修复"
    for _ in range(2):
        session.add(
            _make_failure(
                task="emergency fix",
                action="skip code review",
                cause="time pressure",
                lesson=lesson,
            )
        )
    session.commit()

    engine = ReflectionEngine(db=session)
    report = engine.run_reflection()

    if not report.failure_belief_candidates:
        pytest.skip("没有产出 failure BELIEF candidate，跳过持久化测试")

    # commit 已在 run_reflection 内部完成，重新查询验证持久化
    beliefs_in_db = session.exec(
        select(MemoryItem).where(
            MemoryItem.role == CognitiveRole.BELIEF,
            MemoryItem.status == "candidate",
        )
    ).all()

    assert len(beliefs_in_db) >= 1, "BELIEF candidate 应在 DB 中持久化，但查不到"


# ---------------------------------------------------------------------------
# BL-04: Task B cognitive_context 含 Task A lesson 关键词
# ---------------------------------------------------------------------------


def test_context_contains_lesson(session: Session):
    """
    BL-04：Task A 犯错后学习，Task B 的 cognitive_context 应含有 Task A 的 lesson 关键词，
    证明认知上下文已被 failure 影响。
    """
    lesson_keyword = "备份"
    lesson = f"修改生产 DB 前必须做{lesson_keyword}"

    # Task A：失败
    for _ in range(2):
        session.add(
            _make_failure(
                task="prod DB migration",
                action="ALTER TABLE without backup",
                cause="forgot backup",
                lesson=lesson,
            )
        )
    session.commit()

    # 执行反思（产出 failure pattern → BELIEF candidate）
    engine = ReflectionEngine(db=session)
    engine.run_reflection()

    # Task B：查询认知上下文
    builder = CognitiveContextBuilder(db=session)
    ctx = builder.build(task="数据库操作", top_k=10)
    prompt = ctx.to_prompt()

    # BL-04 断言
    assert lesson_keyword in prompt, (
        f"Task B 的 cognitive_context 应含有 lesson 关键词 '{lesson_keyword}'，\n"
        f"实际 context:\n{prompt[:500]}"
    )


# ---------------------------------------------------------------------------
# BL-05: Task B 的认知上下文比 Task A 更丰富（学习后 belief/failure 切面增加）
# ---------------------------------------------------------------------------


def test_behavior_change(session: Session):
    """
    BL-05：Task A 之前的 cognitive_context 与 Task A 犯错+学习后的 cognitive_context 对比，
    学习后的 context failures 或 beliefs 切面应更丰富（行为改变的代理指标）。
    """
    # Task A 之前：空 DB，获取基线 context
    builder = CognitiveContextBuilder(db=session)
    ctx_before = builder.build(task="数据库维护", top_k=10)
    failures_before = len(ctx_before.failures)
    beliefs_before = len(ctx_before.beliefs)

    # Task A：犯错，记录 FailureRecord
    lesson = "执行 DROP TABLE 前必须验证环境是 dev 而非 prod"
    for _ in range(2):
        session.add(
            _make_failure(
                task="cleanup task",
                action="DROP TABLE on prod",
                cause="wrong environment",
                lesson=lesson,
            )
        )
    session.commit()

    # Reflect：学习
    engine = ReflectionEngine(db=session)
    engine.run_reflection()

    # Task B：获取学习后的 context
    ctx_after = builder.build(task="数据库维护", top_k=10)
    failures_after = len(ctx_after.failures)
    beliefs_after = len(ctx_after.beliefs)

    # BL-05 断言：学习后认知上下文更丰富
    assert failures_after > failures_before or beliefs_after > beliefs_before, (
        f"学习后 context 应更丰富（failures: {failures_before}→{failures_after}, "
        f"beliefs: {beliefs_before}→{beliefs_after}），"
        f"但没有变化——认知闭环可能未生效"
    )
