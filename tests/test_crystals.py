"""技能结晶（v0.7 Ticket 02）测试。

检测/候选纯函数直调不 mock；detect 落库与 decide 用真实 SQLite+FTS
（仅 mock embedding/向量存储两个外部依赖，覆盖 create_skill 链路）。
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, func, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem, SkillCrystal


@pytest.fixture()
def crystal_env():
    import lantai.models.tables  # noqa: F401
    from lantai.storage.fts import init_fts
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock):
        yield session_factory, engine


def _m(mid, content, lane="fact"):
    return MemoryItem(
        id=mid, memory_type="semantic", key=f"k-{mid}", content=content,
        lane=lane, status="active", importance=0.5, decay_score=1.0,
        decay_class="episodic", use_count=0,
        created_at=datetime.now(UTC) - timedelta(days=1),
        updated_at=datetime.now(UTC),
    )


# ── 纯函数：聚类 + 候选形状（零 DB 零 LLM）────────────────

def test_detect_pure_cluster_and_candidate():
    from lantai.evolution.autodream import cluster_memories
    from lantai.services.crystal_service import build_crystal_candidates
    items = [
        _m("m1", "发布会在周五下午两点开始"),
        _m("m2", "发布会需要提前一天彩排"),
        _m("m3", "发布会结束后要写复盘"),
        _m("m4", "发布会预算已批准"),
        _m("x1", "今天天气不错", lane="chat"),  # 噪声 lane
    ]
    clusters = cluster_memories(items, min_size=3)
    assert len(clusters) == 1  # chat 不参与聚类
    cands = build_crystal_candidates(clusters)
    assert cands[0]["candidate_count"] == 4
    assert cands[0]["skill_name"].startswith("crystallized-fact-")
    assert cands[0]["trigger_rule"]
    assert cands[0]["procedure"]
    assert len(cands[0]["sample_keys"]) <= 10


# ── 落库（真实 SQLite，仅 mock 外部依赖）────────────────

def test_detect_dry_run_no_write(crystal_env):
    session_factory, _ = crystal_env
    with session_factory() as s:
        s.add(_m("m1", "发布会在周五下午两点开始"))
        s.add(_m("m2", "发布会需要提前一天彩排"))
        s.add(_m("m3", "发布会结束后要写复盘"))
        s.commit()
    from lantai.services.crystal_service import run_crystal_detect_once
    out = run_crystal_detect_once(dry_run=True)
    assert out["clusters"] >= 1
    assert out["created"] == 0
    with session_factory() as s:
        assert s.exec(select(func.count()).select_from(SkillCrystal)).one() == 0


def test_detect_writes_candidates_idempotent(crystal_env):
    session_factory, _ = crystal_env
    with session_factory() as s:
        s.add(_m("m1", "发布会在周五下午两点开始"))
        s.add(_m("m2", "发布会需要提前一天彩排"))
        s.add(_m("m3", "发布会结束后要写复盘"))
        s.commit()
    from lantai.services.crystal_service import run_crystal_detect_once
    out = run_crystal_detect_once(dry_run=False)
    assert out["created"] >= 1
    out2 = run_crystal_detect_once(dry_run=False)
    assert out2["created"] == 0 and out2["updated"] >= 1  # 幂等 upsert
    with session_factory() as s:
        row = s.exec(select(SkillCrystal)).first()
        assert row.status == "candidate"
        assert row.hit_count >= 2


def test_decide_reject_archives(crystal_env):
    session_factory, _ = crystal_env
    from lantai.core.ids import new_id
    from lantai.services.crystal_service import decide_crystal
    with session_factory() as s:
        s.add(SkillCrystal(id=new_id("crystal"), skill_name="cand-x",
                           trigger_rule="t", procedure="p", candidate_count=3))
        s.commit()
    # 先取真实 id
    with session_factory() as s:
        cid = s.exec(select(SkillCrystal)).first().id
    out = decide_crystal(cid, approve=False, reason="不需要")
    assert out["ok"] is True
    with session_factory() as s:
        row = s.get(SkillCrystal, cid)
        assert row.status == "archived"
        assert row.decision_reason == "不需要"


def test_decide_approve_requires_steps(crystal_env):
    session_factory, _ = crystal_env
    from lantai.core.ids import new_id
    from lantai.services.crystal_service import decide_crystal
    with session_factory() as s:
        s.add(SkillCrystal(id=new_id("crystal"), skill_name="cand-y",
                           trigger_rule="t", procedure="p", candidate_count=3))
        s.commit()
    with session_factory() as s:
        cid = s.exec(select(SkillCrystal)).first().id
    with pytest.raises(ValueError, match="steps"):
        decide_crystal(cid, approve=True, steps=[])  # 宁 miss 不脏写


def test_decide_approve_creates_skill(crystal_env):
    session_factory, _ = crystal_env
    from lantai.core.ids import new_id
    from lantai.services.crystal_service import decide_crystal
    with session_factory() as s:
        s.add(SkillCrystal(id=new_id("crystal"), skill_name="cand-z",
                           trigger_rule="发布流程", procedure="p", candidate_count=3))
        s.commit()
    with session_factory() as s:
        cid = s.exec(select(SkillCrystal)).first().id
    out = decide_crystal(cid, approve=True, steps=["先彩排", "再发布"])
    assert out["ok"] is True
    assert out["skill"]["ok"] is True
    with session_factory() as s:
        row = s.get(SkillCrystal, cid)
        assert row.status == "approved"
        skills = s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "skill")).all()
        assert len(skills) == 1
        assert skills[0].structure["name"] == "cand-z"
