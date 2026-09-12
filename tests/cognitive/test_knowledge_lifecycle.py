"""
tests/cognitive/test_knowledge_lifecycle.py
v0.4 Knowledge Lifecycle 状态机冒烟测试（全不 mock）

LC-01: Active → Weakened（confidence < threshold）
LC-02: Weakened → Superseded（新 Belief 取代旧 Belief）
LC-03: Superseded → Retired（decay_score 极低触发退役）
LC-04: candidate → Active（promote，Candidate != Knowledge）
LC-05: lifecycle_status 过滤（Retired 不参与活跃检索）
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.cognition.lifecycle import KnowledgeLifecycleManager, LifecycleTransitionError
from lantai.core.ids import new_id
from lantai.models.tables import CognitiveRole, LifecycleStatus, MemoryItem


@pytest.fixture(name="engine")
def engine_fixture():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture(name="session")
def session_fixture(engine):
    with Session(engine) as s:
        yield s


def _make_belief(
    content: str, confidence: float = 0.8, status: str = "active", decay_score: float = 1.0
) -> MemoryItem:
    return MemoryItem(
        id=new_id("mem"),
        content=content,
        role=CognitiveRole.BELIEF,
        confidence=confidence,
        status=status,
        lifecycle_status=LifecycleStatus.ACTIVE if status == "active" else LifecycleStatus.ACTIVE,
        decay_score=decay_score,
    )


def test_active_to_weakened(session: Session):
    """LC-01: confidence 低于 0.5 触发 weaken()，lifecycle_status → WEAKENED"""
    item = _make_belief("需要备份的规则", confidence=0.4)
    session.add(item)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    mgr.weaken(item)
    session.commit()

    refreshed = session.get(MemoryItem, item.id)
    assert refreshed.lifecycle_status == LifecycleStatus.WEAKENED
    assert refreshed.weakened_at is not None, "weakened_at 必须被设置"


def test_weakened_to_superseded(session: Session):
    """LC-02: 新 Belief 取代旧 Belief，旧记忆进入 SUPERSEDED 状态"""
    old_belief = _make_belief("旧规则：直接修改配置", confidence=0.4)
    new_belief = _make_belief("新规则：修改前必须备份配置", confidence=0.9)
    session.add(old_belief)
    session.add(new_belief)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    mgr.weaken(old_belief)
    mgr.supersede(old_belief, new_belief)
    session.commit()

    refreshed = session.get(MemoryItem, old_belief.id)
    assert refreshed.lifecycle_status == LifecycleStatus.SUPERSEDED
    assert refreshed.superseded_by == new_belief.id, "superseded_by 必须指向新记忆"
    assert refreshed.superseded_at is not None, "superseded_at 必须被设置"


def test_superseded_to_retired(session: Session):
    """LC-03: Superseded + decay_score 极低 → Retired"""
    item = _make_belief("已过期的规则", confidence=0.3, decay_score=0.1)
    item.lifecycle_status = LifecycleStatus.SUPERSEDED
    session.add(item)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    mgr.retire(item)
    session.commit()

    refreshed = session.get(MemoryItem, item.id)
    assert refreshed.lifecycle_status == LifecycleStatus.RETIRED
    assert refreshed.retired_at is not None, "retired_at 必须被设置"


def test_promotion_candidate_to_active(session: Session):
    """LC-04: candidate 记忆经 promote() 晋升为 Active（Candidate != Knowledge）"""
    candidate = _make_belief(
        "候选信念：持续集成比手动部署更可靠", confidence=0.7, status="candidate"
    )
    session.add(candidate)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    mgr.promote(candidate)
    session.commit()

    refreshed = session.get(MemoryItem, candidate.id)
    assert refreshed.status == "active", "promote 后 status 应为 active"
    assert refreshed.lifecycle_status == LifecycleStatus.ACTIVE


def test_active_cannot_retire_directly(session: Session):
    """LC-04b: Active 记忆不能直接退役（必须先 weaken/supersede）"""
    item = _make_belief("不能直接退役的规则")
    session.add(item)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    with pytest.raises(LifecycleTransitionError, match="Active"):
        mgr.retire(item)


def test_retired_excluded_from_active_query(session: Session):
    """LC-05: Retired 记忆不参与活跃检索（lifecycle_status=active 过滤）"""
    active_item = _make_belief("仍然有效的规则", confidence=0.9)
    retired_item = _make_belief("已退役的规则", confidence=0.2)
    retired_item.lifecycle_status = LifecycleStatus.RETIRED
    session.add(active_item)
    session.add(retired_item)
    session.commit()

    active_only = session.exec(
        select(MemoryItem).where(MemoryItem.lifecycle_status == LifecycleStatus.ACTIVE)
    ).all()

    ids = [m.id for m in active_only]
    assert active_item.id in ids, "Active 记忆应在结果中"
    assert retired_item.id not in ids, "Retired 记忆不应在活跃查询结果中"


def test_scan_and_weaken_batch(session: Session):
    """LC-06: scan_and_weaken 批量扫描：低置信度记忆自动 weaken"""
    strong = _make_belief("强信念", confidence=0.9)
    weak = _make_belief("弱信念", confidence=0.3)
    session.add(strong)
    session.add(weak)
    session.commit()

    mgr = KnowledgeLifecycleManager(db=session)
    weakened = mgr.scan_and_weaken([strong, weak])
    session.commit()

    assert len(weakened) == 1
    assert weakened[0].id == weak.id
    assert session.get(MemoryItem, strong.id).lifecycle_status == LifecycleStatus.ACTIVE
