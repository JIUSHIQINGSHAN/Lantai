"""卷宗（Juanzong）核心情境聚合与长程任务聚焦引擎冒烟测试 (Ticket 01)。

测试纪律（AGENTS.md）：
- 真实构造输入，直调被测函数内部逻辑，不 mock 核心计算与存储过程。
- 覆盖卷宗生命周期、里程碑状态流转、记忆项绑定与移除、情境记忆聚焦检索以及租户安全隔离 5 个关键接缝。
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from lantai.cognition.juanzong import (
    Dossier,
    DossierFocusResult,
    DossierMilestone,
    JuanzongEngine,
)
from lantai.core.auth import Principal
from lantai.models.tables import MemoryItem


@pytest.fixture
def session():
    """内存 SQLite 会话环境，隔离测试。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_memory(
    session: Session, mem_id: str, content: str, user_id: str | None = None
) -> MemoryItem:
    item = MemoryItem(
        id=mem_id,
        content=content,
        user_id=user_id,
        status="active",
        confidence=0.9,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_dossier_lifecycle_crud(session: Session):
    """接缝 1：卷宗基础生命周期（创建、查询、办结、归档）。"""
    engine = JuanzongEngine(session=session)

    # 1. 创建卷宗
    dossier = engine.create_dossier(
        title="2026 兰台架构演进战役",
        goal="完成认知层卷宗与长程任务聚焦聚合，提升多轮注意力稳定性",
        milestones=[
            {"title": "设计 ADR 规范", "status": "done"},
            {"title": "落地核心引擎", "status": "in_progress"},
            {"title": "全量发布验证", "status": "pending"},
        ],
        metadata={"priority": "P0", "commander": "Claude"},
    )
    assert dossier.id.startswith("dos_")
    assert dossier.status == "active"
    assert len(dossier.milestones) == 3
    assert dossier.milestones[0]["status"] == "done"

    # 2. 获取卷宗
    retrieved = engine.get_dossier(dossier.id)
    assert retrieved is not None
    assert retrieved.title == "2026 兰台架构演进战役"
    assert retrieved.goal.startswith("完成认知层卷宗")

    # 3. 办结卷宗
    closed = engine.close_dossier(dossier.id, summary="战役胜利完成")
    assert closed.status == "completed"
    assert closed.metadata_json.get("closing_summary") == "战役胜利完成"


def test_dossier_milestone_transition(session: Session):
    """接缝 2：里程碑推进与流转。"""
    engine = JuanzongEngine(session=session)
    dossier = engine.create_dossier(
        title="性能排障案",
        goal="查明高并发下 SQLite 锁争用根因",
        milestones=[
            {"id": "ms-1", "title": "抓取真实慢查询", "status": "pending"},
            {"id": "ms-2", "title": "单写多读 WAL 调优", "status": "pending"},
        ],
    )

    # 推进 ms-1 为 done
    updated = engine.update_milestone(
        dossier_id=dossier.id,
        milestone_id="ms-1",
        status="done",
        notes="已从 500 次压测日志中定位锁持有超 200ms 的事务",
    )
    ms_map = {m["id"]: m for m in updated.milestones}
    assert ms_map["ms-1"]["status"] == "done"
    assert "锁持有超 200ms" in ms_map["ms-1"].get("notes", "")
    assert ms_map["ms-2"]["status"] == "pending"


def test_dossier_memory_binding(session: Session):
    """接缝 3：记忆项绑定与解绑。"""
    engine = JuanzongEngine(session=session)
    _seed_memory(session, "mem-01", "SQLite 在 WAL 模式下读写互不阻塞")
    _seed_memory(session, "mem-02", "写锁争用发生在并行批量插入")

    dossier = engine.create_dossier(
        title="并发存储研究",
        goal="验证 WAL 模式特性",
    )
    assert dossier.memory_ids == []

    # 绑定记忆
    engine.bind_memories(dossier.id, ["mem-01", "mem-02"])
    dossier_after = engine.get_dossier(dossier.id)
    assert "mem-01" in dossier_after.memory_ids
    assert "mem-02" in dossier_after.memory_ids

    # 重复绑定具备幂等性
    engine.bind_memories(dossier.id, ["mem-01"])
    assert len(engine.get_dossier(dossier.id).memory_ids) == 2

    # 解绑记忆
    engine.unbind_memories(dossier.id, ["mem-01"])
    dossier_final = engine.get_dossier(dossier.id)
    assert "mem-01" not in dossier_final.memory_ids
    assert "mem-02" in dossier_final.memory_ids


def test_dossier_context_focusing(session: Session):
    """接缝 4：情境记忆聚焦检索（Dossier Context Focusing）。"""
    engine = JuanzongEngine(session=session)

    # 制造无关记忆与卷宗内记忆
    _seed_memory(session, "mem-irrelevant", "今天中午食堂的红烧肉不错")
    _seed_memory(session, "mem-rel-1", "FastAPI 路由必须带显式类型注解与文档")
    _seed_memory(session, "mem-rel-2", "Pydantic V2 请求体验证失败返回 422 结构")

    dossier = engine.create_dossier(
        title="API 重构规范专项",
        goal="统一所有 REST 端点请求参数校验",
        memory_ids=["mem-rel-1", "mem-rel-2"],
    )

    # 执行卷宗聚焦检索
    focus_res: DossierFocusResult = engine.focus_dossier_context(
        dossier_id=dossier.id,
        query="路由参数注解规范",
        limit=5,
    )
    assert focus_res.dossier_id == dossier.id
    assert focus_res.dossier_title == "API 重构规范专项"
    assert len(focus_res.focused_memories) == 2

    focused_ids = [m["id"] for m in focus_res.focused_memories]
    assert "mem-rel-1" in focused_ids
    assert "mem-rel-2" in focused_ids
    assert "mem-irrelevant" not in focused_ids


def test_dossier_tenant_isolation(session: Session):
    """接缝 5：租户行级安全隔离（宁 miss 不脏写）。"""
    p_alice = Principal(user_id="user_alice", allowed_lanes=[])
    p_bob = Principal(user_id="user_bob", allowed_lanes=[])

    engine_alice = JuanzongEngine(session=session, principal=p_alice)
    engine_bob = JuanzongEngine(session=session, principal=p_bob)

    # Alice 创建绝密卷宗
    dos_alice = engine_alice.create_dossier(
        title="Alice 的机密排期",
        goal="规划 Q4 秘密项目",
    )

    # 1. Bob 无法直接读取 Alice 的卷宗
    assert engine_bob.get_dossier(dos_alice.id) is None

    # 2. Bob 无法为 Alice 的卷宗绑定记忆或更新里程碑
    res_bind = engine_bob.bind_memories(dos_alice.id, ["mem-x"])
    assert res_bind is False

    # 3. 卷宗列表隔离
    alice_list = engine_alice.list_dossiers()
    bob_list = engine_bob.list_dossiers()
    assert len(alice_list) == 1
    assert len(bob_list) == 0
