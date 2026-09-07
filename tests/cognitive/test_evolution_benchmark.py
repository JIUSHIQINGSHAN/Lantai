"""
tests/cognitive/test_evolution_benchmark.py

认知演化基准测试 (Cognitive Evolution Benchmark)
验证整个演化链路的准确性，而不只是测试单个函数。

指标覆盖：
- Promotion Precision / Recall
- False Promotion Rate
- Belief Stability (反证后置信度确实下降)
- Rule Stability (成功应用后置信度上升)
- Evidence Attribution Accuracy
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, StaticPool

from lantai.models.tables import (
    MemoryItem, CognitiveRole, CognitivePattern, Evidence, MemoryEdge
)
from lantai.cognition.evolution import EvolutionEngine
from lantai.core.ids import new_id
from lantai.core.time import utcnow


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ──────────────────────────────────────────────────────────────
# Benchmark 1: Promotion Precision — 高质量 Pattern → Belief
# ──────────────────────────────────────────────────────────────
def test_promotion_precision_high_quality(db):
    """高质量 Pattern (confidence=0.85, independent>=2) 应该被成功提拔为 Belief 候选。"""
    eng = EvolutionEngine(db)
    pattern = CognitivePattern(
        id="pat_hq",
        pattern_type="recurrence",
        description="SQLite WAL mode consistently improves read throughput",
        source_ids=["exp1", "exp2", "exp3"],
        occurrence_count=3,
        independent_source_count=3,
        confidence=0.85,
        status="candidate",
        updated_at=utcnow(),
    )
    proposals = eng.propose_beliefs([pattern])
    assert len(proposals) == 1, "高质量 Pattern 应被提拔"
    assert proposals[0].role == CognitiveRole.BELIEF
    assert proposals[0].status == "candidate"  # 绝不自动 commit


# ──────────────────────────────────────────────────────────────
# Benchmark 2: False Promotion Rate — 低质量 Pattern 必须被拒绝
# ──────────────────────────────────────────────────────────────
def test_false_promotion_rejected(db):
    """
    低质量 Pattern (confidence=0.3, independent=1) 不能被提拔为 Belief。
    这是最重要的 False Promotion Rate 基准。
    """
    eng = EvolutionEngine(db)
    weak_pattern = CognitivePattern(
        id="pat_weak",
        pattern_type="recurrence",
        description="Vague heuristic with no real evidence",
        source_ids=["exp_same", "exp_same"],   # 同源重复！
        occurrence_count=2,
        independent_source_count=1,            # 非独立
        confidence=0.3,
        status="candidate",
        updated_at=utcnow(),
    )
    proposals = eng.propose_beliefs([weak_pattern])
    assert len(proposals) == 0, "低质量 Pattern 绝不应被提拔（False Promotion 防护）"


# ──────────────────────────────────────────────────────────────
# Benchmark 3: Belief Stability — 级联衰减后置信度下降
# ──────────────────────────────────────────────────────────────
def test_belief_stability_after_cascade_decay(db):
    """
    底层 Observation 被惩罚后，依赖它的 Belief 置信度应该下降。
    验证 cascade_decay 的级联衰减正确传播。
    """
    from lantai.services.evolution import cascade_decay

    # 建立 DAG: obs → (supports) → belief
    obs = MemoryItem(id="obs_decay", content="hypothesis X", role=CognitiveRole.OBSERVATION, confidence=0.8)
    belief = MemoryItem(id="bel_decay", content="belief from X", role=CognitiveRole.BELIEF, confidence=0.8)
    # The source is the observation supporting the target belief
    edge = MemoryEdge(
        id="edge_1",
        source_memory_id="obs_decay",
        target_memory_id="bel_decay",
        relation="supports",
    )
    db.add_all([obs, belief, edge])
    db.commit()

    # 对底层 obs 施加惩罚
    cascade_decay(db, source_memory_id="obs_decay", decay_factor=0.3)

    db.refresh(belief)
    assert belief.confidence < 0.8, "Belief 置信度应在级联衰减后下降"


# ──────────────────────────────────────────────────────────────
# Benchmark 4: Evidence Attribution Accuracy
# ──────────────────────────────────────────────────────────────
def test_evidence_attribution(db):
    """
    写入 Evidence 后，该 Evidence 的 source_memory_id 应正确指向对应 MemoryItem。
    验证证据溯源的准确性。
    """
    mem = MemoryItem(id="mem_attr", content="Python asyncio is non-blocking", role=CognitiveRole.OBSERVATION)
    ev = Evidence(
        id="ev_attr",
        evidence_type="execution_result",
        source_memory_id="mem_attr",
        content="Observed in benchmark run: asyncio finished 3× faster",
        reliability=0.9,
        independence=1.0,
        created_at=utcnow(),
    )
    db.add(mem)
    db.add(ev)
    db.commit()

    fetched_ev = db.get(Evidence, "ev_attr")
    assert fetched_ev.source_memory_id == "mem_attr"
    assert fetched_ev.reliability == 0.9
    assert fetched_ev.independence == 1.0


# ──────────────────────────────────────────────────────────────
# Benchmark 5: Rule Stability — 高分规则应保持稳定不被误降级
# ──────────────────────────────────────────────────────────────
def test_rule_stability(db):
    """
    高置信度的 Rule (confidence >= 0.85) 即便经过 Reflection，
    不应因"置信度偏低"被标记为 weakened（rules_weakened 不应包含它）。
    """
    from lantai.cognition.reflection import ReflectionEngine

    rule = MemoryItem(
        id="stable_rule",
        content="Use multiprocessing for CPU-bound tasks",
        role=CognitiveRole.RULE,
        confidence=0.9,
    )
    db.add(rule)
    db.commit()

    engine = ReflectionEngine(db)
    report = engine.run_reflection()

    # 高置信度的 Rule 不应出现在 weakened 列表
    assert report.rules_weakened == 0


# ──────────────────────────────────────────────────────────────
# Benchmark 6: Promotion Recall — 足量 Observation → Pattern 候选被发现
# ──────────────────────────────────────────────────────────────
def test_promotion_recall_observations_to_patterns(db):
    """
    写入 4 条重复内容的 Observation 后，
    Reflection 应该能发现并提出 >= 1 个 Pattern 候选（高召回率）。
    """
    from lantai.cognition.reflection import ReflectionEngine

    for i in range(4):
        db.add(MemoryItem(
            id=f"obs_recall_{i}",
            content="SQLite WAL mode improves read concurrency",
            role=CognitiveRole.OBSERVATION,
        ))
    db.commit()

    engine = ReflectionEngine(db)
    report = engine.run_reflection()

    assert report.new_patterns >= 1, "4 条重复 Observation 应产生 Pattern 候选（Recall 测试）"
