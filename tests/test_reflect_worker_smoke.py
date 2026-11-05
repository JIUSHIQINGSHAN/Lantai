"""反思 worker 端到端执行冒烟测试（不 mock 内部逻辑）。"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import (
    ConflictEvent,
    MemoryItem,
    MemoryProposal,
    ReflectRun,
)


@pytest.fixture
def worker_db_env(monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session。"""
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    yield session_factory, engine


def test_reflect_worker_end_to_end_smoke(worker_db_env, monkeypatch):
    """端到端冒烟：种子数据包含 open 冲突，执行 worker 后健康快照透出，冲突被提出反思提案。"""
    session_factory, _ = worker_db_env
    now = utcnow()

    # 1. 写入一条待反思的记忆与关联冲突事件
    mem_id = new_id("mem")
    conflict_id = new_id("conf")
    with session_factory() as s:
        mem = MemoryItem(
            id=mem_id,
            key="test_conflict_key",
            content="这是一条具有未决事实冲突的记忆",
            lane="general",
            status="active",
            importance=0.8,
            created_at=now,
            updated_at=now,
        )
        conf = ConflictEvent(
            id=conflict_id,
            memory_id=mem_id,
            status="open",
            detail="与既有事实断言矛盾",
            created_at=now,
        )
        s.add_all([mem, conf])
        s.commit()

    # 2. 模拟外部 LLM 输出（curator 产出 deprecate/merge 提案，rejecter 通过）
    curate_payload = {
        "proposals": [
            {
                "proposal_type": "deprecate",
                "target_memory_id": mem_id,
                "new_content": "",
                "memory_type": "semantic",
                "reason": "存在无法调和的冲突，予以弃用",
                "confidence": 0.85,
                "evidence_ids": [mem_id],
            }
        ]
    }
    reject_payload = {"accept": True, "risk": "low", "reason": "冲突事实明确，准予弃用"}

    from lantai.workers.reflect_worker import run_reflect_once

    with (
        patch("lantai.evolution.reflector.chat_json", side_effect=[curate_payload, reject_payload]),
        patch("lantai.storage.vector_store.ChromaVectorStore", Mock()),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
    ):
        res = run_reflect_once()

    # 3. 验证返回结构与健康透出
    assert res["ok"] is True
    assert res["skipped"] is False
    assert res["proposals_created"] == 1
    assert "health_before" in res
    assert "health_after" in res
    assert res["health_before"]["open_conflicts"] == 1

    # 4. 验证数据库中落库了 ReflectRun 与 MemoryProposal
    with session_factory() as s:
        run_record = s.exec(select(ReflectRun)).one()
        assert run_record.source == "scheduled"
        assert run_record.proposals_created == 1
        assert run_record.health_before["open_conflicts"] == 1

        proposal = s.exec(select(MemoryProposal)).one()
        assert proposal.target_memory_id == mem_id
        assert proposal.proposal_type == "deprecate"
        assert proposal.decided_by == "reflect"


def _run_worker_on_empty_db():
    """空库真实执行 worker；仅屏蔽外部网络（LLM/embedding/向量库）。"""
    from lantai.workers.reflect_worker import run_reflect_once

    with (
        patch("lantai.evolution.reflector.chat_json", return_value={"proposals": []}),
        patch("lantai.storage.vector_store.ChromaVectorStore", Mock()),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
    ):
        return run_reflect_once()


def test_reflect_worker_cognitive_reflection_normal_path(worker_db_env):
    """不 mock 冒烟：认知反思主路径真实执行，结果带 cognitive_reflection 统计。"""
    res = _run_worker_on_empty_db()

    assert res["ok"] is True
    cog = res["cognitive_reflection"]
    assert "ok" not in cog  # 成功路径结构保持不变
    for key in (
        "new_patterns",
        "belief_candidates",
        "rule_candidates",
        "rules_weakened",
        "failures",
        "failures_scoped",
        "summary",
    ):
        assert key in cog


def test_reflect_worker_cognitive_reflection_failure_not_silent(worker_db_env, monkeypatch, caplog):
    """认知反思抛错时：留日志 + 结构化失败，且不破坏 worker 其余结果。

    monkeypatch 仅用于外部故障注入（强制 ReflectionEngine 抛错），不跳过被测逻辑。
    """
    from lantai.cognition.reflection import ReflectionEngine

    def _boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(ReflectionEngine, "run_reflection", _boom)

    with caplog.at_level("ERROR", logger="lantai"):
        res = _run_worker_on_empty_db()

    assert res["ok"] is True
    assert res["skipped"] == "idle"  # 主反思结果原样保留
    assert "health" in res
    assert res["cognitive_reflection"] == {"ok": False, "error": "RuntimeError: boom"}
    assert any(
        "cognitive reflection failed" in r.getMessage() and r.exc_info for r in caplog.records
    )
