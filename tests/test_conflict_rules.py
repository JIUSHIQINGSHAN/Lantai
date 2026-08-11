"""冲突消解确定性层（P0-2）冒烟测试（不 mock 内部逻辑）。

- check_rules 纯函数：规则命中（双向）/ 未命中 / 开关关闭
- decide() 集成：规则命中短路 LLM；未命中回落 LLM（mock 仅外部 LLM）
- ConflictEvent 账本落库 + service 裁决
"""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.models.tables import ConflictEvent, MemoryCandidate, MemoryItem


@pytest.fixture()
def conflict_env():
    """内存 SQLite 真实建表 + patch db.get_session（仅隔离 DB）。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine


def _seed(conflict_env, existing_content: str, summary: str):
    session_factory, engine = conflict_env
    with session_factory() as s:
        s.add(MemoryItem(
            id=new_id("mem"), memory_type="semantic", key="k1",
            content=existing_content, lane="fact", status="active",
            importance=0.5, use_count=0, decay_score=1.0))
        s.add(MemoryCandidate(
            id=new_id("cand"), document_id="d1", summary=summary,
            extractor_confidence=0.9, lane="fact"))
        s.commit()
        return s.exec(select(MemoryCandidate)).first().id


def test_check_rules_hit_both_directions(conflict_env):
    from lantai.gate.conflict_rules import check_rules
    hits = check_rules("新系统启用登录限制", "旧系统已禁用该功能")
    assert any(h["rule_name"] == "status_switch" for h in hits)
    assert hits[0]["new_matched"] == "启用"
    assert hits[0]["old_matched"] == "禁用"

    hits2 = check_rules("新系统禁用导出", "旧系统启用导出")
    assert hits2[0]["new_matched"] == "禁用"
    assert hits2[0]["old_matched"] == "启用"


def test_check_rules_miss(conflict_env):
    from lantai.gate.conflict_rules import check_rules
    assert check_rules("把端口改为8080", "当前端口是3000") == []


def test_check_rules_disabled(conflict_env, monkeypatch):
    from lantai.gate.conflict_rules import check_rules
    monkeypatch.setattr(settings, "CONFLICT_RULES_ENABLED", False)
    assert check_rules("启用", "禁用") == []


def test_decide_rule_hit_short_circuits_llm(conflict_env):
    """规则命中 → 确定性冲突 + 账本落库；LLM 绝不执行。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed(conflict_env,
                    existing_content="旧策略已禁用自动同步",
                    summary="新策略启用自动同步")
    with patch("lantai.gate.decision.check_contradiction",
               side_effect=AssertionError("LLM must not run when rule hits")):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    assert result["conflicts"][0]["rule_name"] == "status_switch"
    with session_factory() as s:
        evs = s.exec(select(ConflictEvent)).all()
        assert len(evs) == 1
        assert evs[0].status == "open"
        assert evs[0].memory_id == result["conflicts"][0]["memory_id"]


def test_decide_llm_fallback_when_no_rule(conflict_env):
    """规则未命中 → 回落 LLM 矛盾检测（降级不阻断）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed(conflict_env,
                    existing_content="当前端口是3000",
                    summary="把端口改为8080")
    with patch("lantai.gate.decision.check_contradiction",
               return_value={"contradicts": True, "reason": "port changed",
                             "severity": "high"}):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    assert result["conflicts"][0]["reason"] == "port changed"
    # LLM 命中不写确定性账本（规则层未命中）
    with session_factory() as s:
        assert s.exec(select(ConflictEvent)).all() == []


def test_conflict_service_resolve(conflict_env):
    session_factory, engine = conflict_env
    from lantai.services.conflict_service import (
        list_conflict_events, resolve_conflict_event)

    with session_factory() as s:
        ev = ConflictEvent(id=new_id("cfev"), memory_id="m1",
                           rule_name="status_switch")
        s.add(ev)
        s.commit()
        ev_id = ev.id

    lst = list_conflict_events()
    assert len(lst["events"]) == 1
    assert lst["events"][0]["status"] == "open"

    r = resolve_conflict_event(ev_id, "resolved", note="确实矛盾")
    assert r["status"] == "resolved"

    with pytest.raises(ValueError):
        resolve_conflict_event(ev_id, "dismissed")  # 已裁决不可重复裁决
    with pytest.raises(ValueError):
        list_conflict_events(status="nope")          # 非法状态
