"""冲突消解确定性层（P0-2）冒烟测试（不 mock 内部逻辑）。

- check_rules 纯函数：规则命中（双向）/ 未命中 / 开关关闭
- decide() 集成：规则命中短路 LLM；未命中回落 LLM（mock 仅外部 LLM）
- ConflictEvent 账本落库 + service 裁决
"""

import logging
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

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8, [0.1] * 8]),
    ):
        yield session_factory, engine


def _seed(conflict_env, existing_content: str, summary: str):
    session_factory, engine = conflict_env
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=new_id("mem"),
                memory_type="semantic",
                key="k1",
                content=existing_content,
                lane="fact",
                status="active",
                importance=0.5,
                use_count=0,
                decay_score=1.0,
            )
        )
        s.add(
            MemoryCandidate(
                id=new_id("cand"),
                document_id="d1",
                summary=summary,
                extractor_confidence=0.9,
                lane="fact",
            )
        )
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

    cand_id = _seed_imp(
        conflict_env, existing_content="用户不会游泳", summary="用户会游泳", importance=0.5
    )
    with patch(
        "lantai.gate.decision.check_contradiction",
        return_value={"contradicts": True, "reason": "会 vs 不会", "severity": "high"},
    ):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    assert any("negation candidate" in c["reason"] for c in result["conflicts"])


def test_decide_negation_candidate_llm_no_conflict(conflict_env):
    """否定候选 → LLM 判非矛盾 → 放行（"开会" 误候选由 LLM 澄清）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(
        conflict_env, existing_content="他明天不会迟到", summary="明天开会讨论方案", importance=0.5
    )
    with patch(
        "lantai.gate.decision.check_contradiction",
        return_value={"contradicts": False, "reason": "开会≠不会迟到", "severity": "low"},
    ):
        result = decide(cand_id)
    assert result["decision"] != "archive_conflict"


def test_decide_negation_llm_failure_passes(conflict_env):
    """否定候选 + LLM 失败 → 放行（宁 miss，不因探测引入假冲突）。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(
        conflict_env, existing_content="用户不会游泳", summary="用户会游泳", importance=0.5
    )
    with patch("lantai.gate.decision.check_contradiction", side_effect=RuntimeError("llm down")):
        result = decide(cand_id)
    assert result["decision"] != "archive_conflict"


# ── salience 冲突降权（ADR-0020）────────────────────────


def _seed_imp(conflict_env, existing_content: str, summary: str, importance: float):
    session_factory, engine = conflict_env
    with session_factory() as s:
        s.add(
            MemoryItem(
                id=new_id("mem"),
                memory_type="semantic",
                key="k1",
                content=existing_content,
                lane="fact",
                status="active",
                importance=importance,
                use_count=0,
                decay_score=1.0,
            )
        )
        s.add(
            MemoryCandidate(
                id=new_id("cand"),
                document_id="d1",
                summary=summary,
                extractor_confidence=0.9,
                lane="fact",
            )
        )
        s.commit()
        return s.exec(select(MemoryCandidate)).first().id


def test_decide_salience_demote_low_importance(conflict_env):
    """低 salience 旧记忆 + 确定性反义词冲突 → 降权放行，不 archive。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed_imp(
        conflict_env,
        existing_content="旧策略支持自动同步",
        summary="新策略反对自动同步",
        importance=0.3,
    )
    with patch(
        "lantai.gate.decision.check_contradiction",
        side_effect=AssertionError("LLM must not run on deterministic hit"),
    ):
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

    cand_id = _seed_imp(
        conflict_env,
        existing_content="旧策略支持自动同步",
        summary="新策略反对自动同步",
        importance=0.5,
    )
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

    cand_id = _seed_imp(
        conflict_env, existing_content="当前端口是3000", summary="把端口改为8080", importance=0.3
    )
    with patch(
        "lantai.gate.decision.check_contradiction",
        return_value={"contradicts": True, "reason": "port changed", "severity": "high"},
    ):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    with session_factory() as s:
        mem = s.exec(select(MemoryItem)).first()
        assert mem.importance == 0.3  # 未降权


def test_decide_rule_hit_short_circuits_llm(conflict_env):
    """规则命中 → 确定性冲突 + 账本落库；LLM 绝不执行。"""
    session_factory, engine = conflict_env
    from lantai.gate.decision import decide

    cand_id = _seed(
        conflict_env, existing_content="旧策略已禁用自动同步", summary="新策略启用自动同步"
    )
    with patch(
        "lantai.gate.decision.check_contradiction",
        side_effect=AssertionError("LLM must not run when rule hits"),
    ):
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

    cand_id = _seed(conflict_env, existing_content="当前端口是3000", summary="把端口改为8080")
    with patch(
        "lantai.gate.decision.check_contradiction",
        return_value={"contradicts": True, "reason": "port changed", "severity": "high"},
    ):
        result = decide(cand_id)
    assert result["decision"] == "archive_conflict"
    assert result["conflicts"][0]["reason"] == "port changed"
    # LLM 命中不写确定性账本（规则层未命中）
    with session_factory() as s:
        assert s.exec(select(ConflictEvent)).all() == []


# ── 矛盾检测不可用 ≠ 无矛盾（票 .scratch/gate-fail-open/01）────────


class TestContradictionCheckUnavailable:
    """LLM 矛盾检测失败时，`decide()` 不得把「检不了」当「没矛盾」静默放行。

    原实现 `check_contradiction` 的 except 分支返回
    `{"contradicts": False, ...}`，与「检测器说没矛盾」完全同形；`decide()`
    的 site 1（decision.py:119-132）再包一层 try/except，双保险静默。
    LLM 一挂，矛盾检测整条通道失效且日志无痕，候选长驱直入 PROMOTE。

    修法口径：`contradiction.py` 失败返回带 `check_unavailable: True` 标记，
    `decide()` 据此回 REJECT 进待审队列（宁 miss 不脏写：miss 必须留痕）。
    """

    @staticmethod
    def _seed_no_rule_hit(conflict_env):
        """造一对**不命中任何确定性规则/反义词/否定对**的候选，逼进 site 1 LLM。

        反例护栏：这里必须真进 LLM 分支，否则测试会在确定性层短路而假绿。
        """
        session_factory, _ = conflict_env
        from lantai.gate.conflict_rules import (
            check_antonyms,
            check_negation_pairs,
            check_rules,
        )

        existing, summary = "当前端口是3000", "把端口改为8080"
        # 造境自校验：确定性层必须真的不命中
        assert check_rules(summary, existing) == []
        assert check_antonyms(summary, existing) == []
        assert check_negation_pairs(summary, existing) == []

        cand_id = _seed(conflict_env, existing_content=existing, summary=summary)
        return session_factory, cand_id

    def test_llm_failure_yields_reject_not_silent_promote(self, conflict_env, caplog):
        """site 1 LLM 抛异常 → REJECT（原实现静默放行成 WORKING_ONLY）。"""
        session_factory, cand_id = self._seed_no_rule_hit(conflict_env)
        from lantai.gate.decision import decide

        with (
            patch("lantai.gate.decision.check_contradiction", side_effect=RuntimeError("llm down")),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            result = decide(cand_id)

        assert result["decision"] == "reject", (
            "检测器不可用不得伪装成『检测器说没矛盾』——"
            f"应 reject 待审，实得 {result['decision']}"
        )
        assert "contradiction check" in result["reason"].lower()
        assert result.get("check_unavailable") is True

    def test_llm_no_conflict_still_promotes(self, conflict_env):
        """回归护栏：LLM 正常返回「无矛盾」→ 照旧放行，不得误判成 reject。"""
        _, cand_id = self._seed_no_rule_hit(conflict_env)
        from lantai.gate.decision import decide

        with patch(
            "lantai.gate.decision.check_contradiction",
            return_value={"contradicts": False, "reason": "端口变更非矛盾", "severity": "low"},
        ):
            result = decide(cand_id)
        assert result["decision"] != "reject"
        assert result.get("check_unavailable") is not True

    def test_llm_failure_marks_flag_and_logs(self, conflict_env, caplog):
        """`check_contradiction` 本体：失败返回带标记 + warning 留痕（此前零日志）。"""
        from lantai.gate.contradiction import check_contradiction

        with (
            patch("lantai.gate.contradiction.chat_json", side_effect=TimeoutError("llm timeout")),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            out = check_contradiction("新说法", "旧说法")

        assert out.get("check_unavailable") is True
        assert out["contradicts"] is False
        assert "contradiction check unavailable" in caplog.text
        # 成功路径形状未被改动：失败返回仍带齐原有三键
        assert set(out) >= {"contradicts", "reason", "severity"}

    def test_empty_llm_return_is_not_failure(self, conflict_env):
        """反向护栏：LLM 成功但返回空 dict（conftest 替身即此）≠ 检测失败。

        空返回走「无矛盾」正常路径，不得因 `{}` 里没有 contradicts 就判检不了。
        """
        from lantai.gate.contradiction import check_contradiction

        with patch("lantai.gate.contradiction.chat_json", return_value={}):
            out = check_contradiction("新说法", "旧说法")
        assert out.get("check_unavailable") is not True
        assert out == {}

    def test_real_chat_json_failure_propagates_flag_end_to_end(self, conflict_env, caplog):
        """端到端：只 patch 最外层 `chat_json`，中间的 decision.py 双 try 全真实执行。

        这是本票的核心断言——旧的 site-1 except（decision.py:123-124）会把
        标记重新抹平成 `{"contradicts": False}`。抹平即脏写，故必须穿层验证。
        """
        _, cand_id = self._seed_no_rule_hit(conflict_env)
        from lantai.gate.decision import decide

        with (
            patch("lantai.gate.contradiction.chat_json", side_effect=RuntimeError("503")),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            result = decide(cand_id)

        assert result["decision"] == "reject"
        assert "contradiction check" in result["reason"].lower()

    def test_deterministic_hit_still_short_circuits_llm(self, conflict_env):
        """既有护栏不变：确定性规则命中 → LLM 绝不执行，仍 archive_conflict。

        新标记只影响「检不了」路径，不得让规则命中的硬冲突也变成 reject。
        """
        session_factory, _ = conflict_env
        from lantai.gate.decision import decide

        cand_id = _seed(
            conflict_env, existing_content="旧策略已禁用自动同步", summary="新策略启用自动同步"
        )
        with patch(
            "lantai.gate.decision.check_contradiction",
            side_effect=AssertionError("LLM must not run when rule hits"),
        ):
            result = decide(cand_id)
        assert result["decision"] == "archive_conflict"
        assert result.get("check_unavailable") is not True

    def test_negation_site_2_unchanged(self, conflict_env):
        """ADR-0024 不动：否定候选 + LLM 失败 → 仍放行（宁 miss），不因新标记改判。"""
        from lantai.gate.decision import decide

        cand_id = _seed_imp(
            conflict_env, existing_content="用户不会游泳", summary="用户会游泳", importance=0.5
        )
        with patch("lantai.gate.decision.check_contradiction", side_effect=RuntimeError("llm down")):
            result = decide(cand_id)
        assert result["decision"] != "archive_conflict"


class TestContradictionUnavailableReachesPendingReview:
    """LLM 检不了 → 候选落 pending_review（不是被静默丢弃）。"""

    def test_evolve_worker_enqueues_when_check_unavailable(self, param_env, caplog):
        """走真实 worker：decide → reject → enqueue_rejected（含 review_due_at）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(
                MemoryItem(
                    id=new_id("mem"),
                    memory_type="semantic",
                    key="k1",
                    content="当前端口是3000",
                    lane="fact",
                    status="active",
                    importance=0.5,
                    use_count=0,
                    decay_score=1.0,
                )
            )
            s.add(
                MemoryCandidate(
                    id="cand_llm_down",
                    document_id="d1",
                    summary="把端口改为8080",
                    extractor_confidence=0.9,
                    lane="fact",
                    status="new",
                )
            )
            s.commit()

        from lantai.workers.evolve_worker import run_evolve_once

        with (
            patch("lantai.gate.decision.check_contradiction", side_effect=RuntimeError("llm down")),
            caplog.at_level(logging.WARNING, logger="lantai"),
        ):
            run_evolve_once()

        with session_factory() as s:
            c = s.get(MemoryCandidate, "cand_llm_down")
            assert c.status == "pending_review", "检不了必须进待审队列，不得静默丢弃"
            assert c.review_due_at is not None


def test_conflict_service_resolve(conflict_env):
    session_factory, engine = conflict_env
    from lantai.services.conflict_service import list_conflict_events, resolve_conflict_event

    with session_factory() as s:
        ev = ConflictEvent(id=new_id("cfev"), memory_id="m1", rule_name="status_switch")
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
        list_conflict_events(status="nope")  # 非法状态
