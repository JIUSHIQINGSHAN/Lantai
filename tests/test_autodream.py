"""autodream 蒸馏测试：聚类 / 规划纯函数 + 端到端待审落库（不 mock 内部逻辑）。"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.evolution.autodream import (
    cluster_memories,
    plan_distillation,
    run_autodream_once,
)
from lantai.models.tables import MemoryItem, MemoryProposal


@pytest.fixture()
def autodream_env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _mem(content: str, lane: str = "fact", days: int = 0) -> MemoryItem:
    now = utcnow()
    return MemoryItem(
        id=new_id("mem"), memory_type="semantic", namespace="default",
        key=content[:20], content=content, lane=lane, status="active",
        importance=0.5, decay_score=1.0,
        created_at=now - timedelta(days=days),
        updated_at=now - timedelta(days=days),
        last_used_at=now - timedelta(days=days),
    )


def test_cluster_memories_pure():
    """纯函数：同 lane + 共享关键词归簇；跨 lane 不混；min_size 过滤。"""
    items = [
        _mem("服务A 端口 8080", lane="fact"),
        _mem("服务A 端口 9090", lane="fact"),
        _mem("服务A 端口 9090", lane="rule"),   # 同关键词但不同 lane → 不混
        _mem("用户喜欢咖啡", lane="preference"),
        _mem("咖啡是每日习惯", lane="preference"),
        _mem("独条记忆没有同伴", lane="fact"),
    ]
    clusters = cluster_memories(items, min_size=2)
    flat = sorted(tuple(sorted(m.content for m in c)) for c in clusters)
    # 注意：flat 按元组整体排序，『咖』U+5496 < 『服』U+670D，故咖啡簇在前
    assert flat == [
        ("咖啡是每日习惯", "用户喜欢咖啡"),
        ("服务A 端口 8080", "服务A 端口 9090"),
    ]


def test_plan_distillation_pure():
    """纯函数：新值在前、去重、evidence 溯源、置信度随簇大小递增。"""
    cluster = [
        _mem("公司域名是 example.com", days=60),
        _mem("公司域名是 example.com", days=30),   # 重复内容 → 去重
        _mem("公司域名改为 new-example.com", days=0),
    ]
    plan = plan_distillation(cluster)
    assert plan["proposal_type"] == "add"
    assert plan["evidence_ids"] == [m.id for m in cluster]
    content = plan["proposed_patch"]["content"]
    assert content.index("new-example.com") < content.index("example.com")  # 新值在前
    assert content.count("example.com") == 2   # 重复的一条被去重
    assert plan["confidence"] == 0.8           # 0.5 + 0.15 * (3-1)
    assert plan["proposed_patch"]["lane"] == "fact"


def test_run_autodream_dry_run_writes_nothing(autodream_env):
    """dry-run：规划但不落库（宁 miss 不脏写：不写不算错，写才是风险）。"""
    session_factory, _ = autodream_env
    with session_factory() as s:
        s.add_all([_mem("服务A 端口 8080"), _mem("服务A 端口 9090")])
        s.commit()
    result = run_autodream_once(dry_run=True)
    assert result["clusters"] >= 1
    assert result["plans"] >= 1
    assert result["created"] == 0
    with session_factory() as s:
        assert s.exec(select(MemoryProposal)).all() == []


def test_run_autodream_apply_pending(autodream_env):
    """apply：落 pending 提案，绝不自动应用（宁 miss 不脏写）。"""
    session_factory, _ = autodream_env
    with session_factory() as s:
        s.add_all([_mem("服务A 端口 8080"), _mem("服务A 端口 9090")])
        s.commit()
    result = run_autodream_once(dry_run=False)
    assert result["created"] == 1
    with session_factory() as s:
        props = s.exec(select(MemoryProposal)).all()
        assert len(props) == 1
        assert props[0].status == "pending"
        assert props[0].decided_by == "autodream"
        assert props[0].applied_at is None
        assert len(props[0].evidence_ids) == 2


def test_scheduled_worker_creates_pending_and_records_run(autodream_env):
    """周期入口（Fog：7 天周期）：真实库落 pending 提案 + record_run 可观测。

    调度器接线（interval days=7）见 test_scheduler.py TestAutodreamScheduling。
    """
    session_factory, engine = autodream_env
    with session_factory() as s:
        s.add_all([
            _mem("服务A 端口 8080", days=3),
            _mem("服务A 端口 9090", days=2),
            _mem("服务A 端口 9090 再次确认", days=1),
        ])
        s.commit()
    from lantai.workers.autodream_worker import run_autodream_scheduled
    out = run_autodream_scheduled()
    assert out["created"] >= 1
    with session_factory() as s:
        props = s.exec(select(MemoryProposal).where(
            MemoryProposal.decided_by == "autodream",
            MemoryProposal.status == "pending")).all()
        assert props
    from lantai.core.scheduler import get_last_run
    assert get_last_run("autodream") is not None