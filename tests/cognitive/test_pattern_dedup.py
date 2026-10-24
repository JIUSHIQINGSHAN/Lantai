"""反思写库幂等去重（票 .scratch/review-fixes-2026-10/issues/02）。

不 mock 冒烟（测试纪律）：真实内存 SQLite 建表 + 真实 detect_patterns /
run_reflection。聚类是纯词袋签名计算，不依赖外部 LLM/embedding——
零网络依赖，无需任何 mock。

根因：detect_patterns 对每个 pattern 逐条 add+commit 且无去重键，
每晚反思（reflect_worker → run_reflection）对同一批输入重复落库，
cognitive_pattern / belief / rule 候选数据无界膨胀。
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.cognition.evolution import EvolutionEngine
from lantai.cognition.reflection import ReflectionEngine
from lantai.models.tables import CognitivePattern, CognitiveRole, FailureRecord, MemoryItem


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _observations(n=3, content="SQLite WAL mode improves read concurrency"):
    """最小确定性聚类输入：n 条同质 Observation（词袋签名必然同桶）。"""
    return [
        MemoryItem(
            id=f"obs_{i}",
            content=content,
            role=CognitiveRole.OBSERVATION,
            source_ids=[f"src_{i}"],
            status="active",
        )
        for i in range(n)
    ]


def _pattern_rows(session):
    return session.exec(select(CognitivePattern)).all()


def _role_rows(session, role):
    return session.exec(select(MemoryItem).where(MemoryItem.role == role)).all()


def test_detect_patterns_same_input_twice_inserts_once(session):
    """票 02 验收：同一输入连续两轮，第二轮 cognitive_pattern 新增行数 == 0；
    首轮正常落库（幂等不是不写，是不重复写）。"""
    evo = EvolutionEngine(db=session)

    round1 = evo.detect_patterns(_observations())
    rows_after_round1 = _pattern_rows(session)

    assert len(round1) == 1, f"首轮应聚类出 1 个 pattern，got {len(round1)}"
    assert len(rows_after_round1) == 1, f"首轮应正常落库 1 行，got {len(rows_after_round1)}"

    round2 = evo.detect_patterns(_observations())
    rows_after_round2 = _pattern_rows(session)

    assert len(rows_after_round2) - len(rows_after_round1) == 0, (
        f"第二轮重复落库：{len(rows_after_round1)} → {len(rows_after_round2)}"
    )
    assert round2 == [], "已知 pattern 不应再向下游重复发（晋升侧靠自然键兜底）"


def test_run_reflection_second_night_no_duplicate_rows(session):
    """全链路幂等（每晚反思的真实形状）：pattern / belief / rule 候选
    三处写入口，第二晚对同一输入零新增行。"""
    lesson = "部署前必须执行 dry-run"
    for i in range(2):
        session.add(
            FailureRecord(
                id=f"fail_{i}",
                task="deploy",
                action="skip dry-run",
                expected="ok",
                actual="crash",
                cause="",
                lesson=lesson,
                severity=0.7,
            )
        )
    session.add_all(_observations())
    # 种一条高使用率 BELIEF（usefulness=1.0 → 过 0.80 rule 阈值），驱动 rule 候选路径
    session.add(
        MemoryItem(
            id="belief_seed",
            content="高使用率信条",
            role=CognitiveRole.BELIEF,
            status="active",
            confidence=0.9,
            use_count=10,
            helpful_count=10,
        )
    )
    session.commit()

    ReflectionEngine(db=session).run_reflection()

    pat1, belief1, rule1 = (
        _pattern_rows(session),
        _role_rows(session, CognitiveRole.BELIEF),
        _role_rows(session, CognitiveRole.RULE),
    )
    # 首轮正常产出：observation 聚类 + failure lesson 聚类 = 2 个 pattern；
    # belief = 种子 + failure 低阈值晋升 + observation 聚类晋升（0.77 过 0.70 阈值）；
    # rule = 种子高使用率晋升
    assert len(pat1) == 2, f"首轮应落 2 个 pattern，got {len(pat1)}"
    assert len(belief1) == 3, f"首轮应 3 条 BELIEF（种子+两条候选），got {len(belief1)}"
    assert len(rule1) == 1, f"首轮应 1 条 RULE 候选，got {len(rule1)}"

    ReflectionEngine(db=session).run_reflection()

    pat2 = _pattern_rows(session)
    belief2 = _role_rows(session, CognitiveRole.BELIEF)
    rule2 = _role_rows(session, CognitiveRole.RULE)
    assert len(pat2) == len(pat1), f"第二晚 pattern 重复落库：{len(pat1)} → {len(pat2)}"
    assert len(belief2) == len(belief1), f"第二晚 BELIEF 重复落库：{len(belief1)} → {len(belief2)}"
    assert len(rule2) == len(rule1), f"第二晚 RULE 重复落库：{len(rule1)} → {len(rule2)}"


def test_candidates_dedup_by_natural_key_when_pattern_missing(session):
    """候选侧自然键兜底：pattern 行被清掉（如存量库 dedup_key 为 NULL、
    或人工清理）导致 pattern 重新落库时，同内容 belief/rule 候选不重复插行。"""
    lesson = "不能跳过测试直接合并主分支"
    for i in range(2):
        session.add(
            FailureRecord(
                id=f"fail_{i}",
                task=f"merge PR-{i}",
                action="force merge",
                expected="ok",
                actual="revert",
                cause="",
                lesson=lesson,
                severity=0.7,
            )
        )
    session.commit()

    ReflectionEngine(db=session).run_reflection()
    belief1 = _role_rows(session, CognitiveRole.BELIEF)
    assert len(belief1) == 1, "首轮应产出 1 条 failure belief 候选"

    # 模拟 pattern 侧失忆（宁 miss：pattern 重插允许，候选不脏写）
    for pat in _pattern_rows(session):
        session.delete(pat)
    session.commit()

    ReflectionEngine(db=session).run_reflection()

    belief2 = _role_rows(session, CognitiveRole.BELIEF)
    assert len(belief2) == 1, f"同内容候选被重复插入：{len(belief1)} → {len(belief2)}"
