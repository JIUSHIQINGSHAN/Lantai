"""冲突消解确定性层（P0-2）冒烟测试（不 mock 内部逻辑）。

- check_rules 纯函数：规则命中（双向）/ 未命中 / 开关关闭
- decide() 集成：规则命中短路 LLM；未命中回落 LLM（mock 仅外部 LLM）
- ConflictEvent 账本落库 + service 裁决
"""
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

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

    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8, [0.1] * 8]):
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


# ── 反义词碰撞（ADR-0020：jieba 词级互斥，子串不误伤）────────────────

def test_check_antonyms_hit_both_directions(conflict_env):
    from lantai.gate.conflict_rules import check_antonyms
    hits = check_antonyms("我讨厌咖啡", "我喜欢咖啡")
    assert any(h["rule_name"] == "like_hate" for h in hits)
    assert hits[0]["new_matched"] == "讨厌"
    assert hits[0]["old_matched"] == "喜欢"

    hits2 = check_antonyms("新策略反对自动同步", "旧策略支持自动同步")
    assert hits2[0]["rule_name"] == "support_oppose"
    assert hits2[0]["new_matched"] == "反对"
    assert hits2[0]["old_matched"] == "支持"


def test_check_antonyms_no_substring_false_positive(conflict_env):
    """词级匹配：无反义词共现 → 不误报；多字词对稳定成词。"""
    from lantai.gate.conflict_rules import check_antonyms
    # 无任何反义词对共现 → 不命中（子串匹配的"会"∈"开会"误伤不存在）
    assert check_antonyms("明天开会讨论方案", "明天不能缺席") == []
    # 真矛盾（多字词对）→ 命中
    assert check_antonyms("新策略反对自动同步", "旧策略支持自动同步")


def test_check_antonyms_disabled(conflict_env, monkeypatch):
    from lantai.gate.conflict_rules import check_antonyms
    monkeypatch.setattr(settings, "CONFLICT_ANTONYM_ENABLED", False)
    assert check_antonyms("我讨厌咖啡", "我喜欢咖啡") == []


# ── 单字否定对候选探测（ADR-0024：token 级子串 → 候选，交 LLM 裁决）──

def test_check_negation_pairs_hit(conflict_env):
    """jieba 并词场景："我会"→一词，仍命中候选（会∈我会 / 不会∈不会）。"""
    from lantai.gate.conflict_rules import check_negation_pairs
    hits = check_negation_pairs("我会游泳", "我不会游泳")
    assert any(h["rule_name"] == "can_cannot" for h in hits)
    assert hits[0]["kind"] == "negation_candidate"

    hits2 = check_negation_pairs("我是学生", "我不是学生")
    assert any(h["rule_name"] == "be_notbe" for h in hits2)


def test_check_negation_pairs_same_side_no_hit(conflict_env):
    """同一侧共现（都含"会"，无"不会"）→ 非交叉 → 不命中。"""
    from lantai.gate.conflict_rules import check_negation_pairs
    assert check_negation_pairs("我会游泳", "他也会游泳") == []


def test_check_negation_pairs_disabled(conflict_env, monkeypatch):
    from lantai.gate.conflict_rules import check_negation_pairs
    monkeypatch.setattr(settings, "CONFLICT_NEGATION_ENABLED", False)
    assert check_negation_pairs("我会游泳", "我不能游泳") == []


def test_decide_negation_candidate_llm_contradicts(conflict_env):
    """否定候选 → LLM 判矛盾 → archive_conflict（"我会游泳" vs "我不会游泳"）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="用户不会游泳",
                        summary="用户会游泳",
                        importance=0.5)
    with patch("lantai.gate.decision.check_contradiction",
               return_value={"contradicts": True, "reason": "会 vs 不会",
                             "severity": "high"}):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    assert any("negation candidate" in c["reason"] for c in result["conflicts"])


def test_decide_negation_candidate_llm_no_conflict(conflict_env):
    """否定候选 → LLM 判非矛盾 → 放行（"开会" 误候选由 LLM 澄清）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="他明天不会迟到",
                        summary="明天开会讨论方案",
                        importance=0.5)
    with patch("lantai.gate.decision.check_contradiction",
               return_value={"contradicts": False, "reason": "开会≠不会迟到",
                             "severity": "low"}):
        result = decide(cand_id)
    assert result["decision"] != "archive_conflict"


def test_decide_negation_llm_failure_passes(conflict_env):
    """否定候选 + LLM 失败 → 放行（宁 miss，不因探测引入假冲突）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="用户不会游泳",
                        summary="用户会游泳",
                        importance=0.5)
    with patch("lantai.gate.decision.check_contradiction",
               side_effect=RuntimeError("llm down")):
        result = decide(cand_id)
    assert result["decision"] != "archive_conflict"


# ── salience 冲突降权（ADR-0020）────────────────────────

def _seed_imp(conflict_env, existing_content: str, summary: str, importance: float):
    session_factory, engine = conflict_env
    with session_factory() as s:
        s.add(MemoryItem(
            id=new_id("mem"), memory_type="semantic", key="k1",
            content=existing_content, lane="fact", status="active",
            importance=importance, use_count=0, decay_score=1.0))
        s.add(MemoryCandidate(
            id=new_id("cand"), document_id="d1", summary=summary,
            extractor_confidence=0.9, lane="fact"))
        s.commit()
        return s.exec(select(MemoryCandidate)).first().id


def test_decide_salience_demote_low_importance(conflict_env):
    """低 salience 旧记忆 + 确定性反义词冲突 → 降权放行，不 archive。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="旧策略支持自动同步",
                        summary="新策略反对自动同步",
                        importance=0.3)
    with patch("lantai.gate.decision.check_contradiction",
               side_effect=AssertionError("LLM must not run on deterministic hit")):
        result = decide(cand_id)
    assert result["decision"] != "archive_conflict"
    with session_factory() as s:
        evs = s.exec(select(ConflictEvent)).all()
        assert len(evs) == 1
        assert evs[0].status == "resolved"
        assert evs[0].kind == "salience_demote"
        mem = s.exec(select(MemoryItem)).first()
        assert abs(mem.importance - 0.1) < 1e-9  # 0.3 - 0.2 降权（浮点容差）
        from lantai.models.tables import MemoryCheckpoint
        assert s.exec(select(MemoryCheckpoint)).first() is not None  # 可回滚


def test_decide_salience_keeps_archive_for_high(conflict_env):
    """高 salience 旧记忆 + 确定性冲突 → 维持 archive_conflict 人工裁决。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="旧策略支持自动同步",
                        summary="新策略反对自动同步",
                        importance=0.5)
    result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    with session_factory() as s:
        evs = s.exec(select(ConflictEvent)).all()
        assert evs[0].status == "open"
        mem = s.exec(select(MemoryItem)).first()
        assert mem.importance == 0.5  # 不降权


def test_decide_llm_conflict_no_salience_demote(conflict_env):
    """LLM 矛盾（非确定性规则）→ 低 salience 也不降权，维持 archive_conflict。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(conflict_env,
                        existing_content="当前端口是3000",
                        summary="把端口改为8080",
                        importance=0.3)
    with patch("lantai.gate.decision.check_contradiction",
               return_value={"contradicts": True, "reason": "port changed",
                             "severity": "high"}):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    with session_factory() as s:
        mem = s.exec(select(MemoryItem)).first()
        assert mem.importance == 0.3  # 未降权


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
    from lantai.services.conflict_service import list_conflict_events, resolve_conflict_event

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
