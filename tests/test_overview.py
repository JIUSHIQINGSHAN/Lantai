"""轻量概览冒烟测试：真实临时库 + 真实行，不 mock 聚合逻辑。"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from lantai.models.tables import (
    MemoryCandidate,
    MemoryCheckpoint,
    MemoryItem,
    MemoryProposal,
)


@pytest.fixture()
def overview_env():
    """内存 SQLite + 真实建表 + patch db.get_session。"""
    import lantai.models.tables  # noqa: F401  注册全部表
    import lantai.storage.db as db_module

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_module, "get_session", session_factory)
        yield session_factory, engine


def _mem(content: str, lane: str, decay_class: str = "episodic",
         status: str = "active") -> MemoryItem:
    return MemoryItem(
        id=content, memory_type="fact", key=content[:20],
        content=content, lane=lane, status=status,
        decay_class=decay_class,
    )


def test_overview_counts(overview_env):
    """真实插入不同 lane/衰减类/状态 + 待审候选 + 检查点 + 待审提案。"""
    session_factory, _ = overview_env
    with session_factory() as s:
        s.add(_mem("服务A 端口 8080", lane="fact", decay_class="semantic"))
        s.add(_mem("用户喜欢咖啡", lane="preference"))
        s.add(_mem("昨日聊天摘要", lane="chat", status="archived"))
        s.add(MemoryCandidate(id="c1", document_id="d1", status="pending_review",
                              extractor_confidence=0.3))
        s.add(MemoryCheckpoint(id="ck1", memory_id="m1", version=1))
        s.add(MemoryCheckpoint(id="ck2", memory_id="m1", version=2))
        s.add(MemoryProposal(id="p1", proposal_type="merge", status="pending"))
        s.commit()

    from lantai.ops.overview import get_overview
    o = get_overview()

    assert o["memories"]["total"] == 3
    assert o["memories"]["active"] == 2
    assert o["memories"]["archived"] == 1
    assert o["memories"]["by_lane"] == {
        "fact": 1, "preference": 1, "chat": 1}
    assert o["memories"]["by_decay_class"] == {"semantic": 1, "episodic": 2}
    assert o["candidates_pending_review"] == 1
    assert o["checkpoints"] == 2
    assert o["proposals_pending"] == 1


def test_overview_empty(overview_env):
    """空库不炸：全 0 + 空分布。"""
    from lantai.ops.overview import get_overview
    o = get_overview()
    assert o["memories"]["total"] == 0
    assert o["memories"]["active"] == 0
    assert o["memories"]["by_lane"] == {}
    assert o["memories"]["by_decay_class"] == {}
    assert o["candidates_pending_review"] == 0
    assert o["checkpoints"] == 0
    assert o["proposals_pending"] == 0


def test_build_overview_pure(overview_env):
    """build_overview 直收 session：与 get_overview 同口径（不 mock 内部逻辑）。"""
    session_factory, _ = overview_env
    with session_factory() as s:
        s.add(_mem("公司域名是 example.com", lane="fact", decay_class="procedural"))
        s.commit()

    from lantai.ops.overview import build_overview
    with session_factory() as s:
        o = build_overview(s)
    assert o["memories"]["total"] == 1
    assert o["memories"]["by_lane"] == {"fact": 1}
    assert o["memories"]["by_decay_class"] == {"procedural": 1}
