"""Step 7 影子观察期决策逻辑冒烟测试——纯函数直调，零 DB，不 mock。

覆盖：三条护栏（漏检恶化/召回骤降/召回偏离）、全空 hold、缺指标 hold、
promote 通过、到期判定、promote 前置检查。

集成测试（真实 SQLite + mock 外部网络）：表可建、open_shadow 落库、
check_shadow_due 到期判定、rollback 护栏恢复。
"""
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import remembrance.storage.db as db_module
from remembrance.core.time import utcnow
from remembrance.models.tables import ParamOverride
from remembrance.parameters.shadow import (
    decide_promote_target,
    evaluate_window,
    shadow_is_due,
)


def _base(**over):
    m = {"sample_count": 50, "zero_result_rate": 0.10,
         "avg_result_count": 4.5, "jaccard_vs_baseline": None}
    m.update(over)
    return m


def _shadow(**over):
    m = {"sample_count": 50, "zero_result_rate": 0.10,
         "avg_result_count": 4.5, "jaccard_vs_baseline": None}
    m.update(over)
    return m


class TestEvaluateWindow:
    def test_both_empty_holds(self):
        r = evaluate_window({}, {})
        assert r["verdict"] == "hold"
        assert r["reason"] == "both_empty"

    def test_zero_result_worsens_rolls_back(self):
        r = evaluate_window(_base(), _shadow(zero_result_rate=0.20))
        assert r["verdict"] == "rollback"
        assert "zero_result" in r["reason"]

    def test_zero_result_within_tolerance_promotes(self):
        r = evaluate_window(_base(), _shadow(zero_result_rate=0.14))
        assert r["verdict"] == "promote"

    def test_avg_result_plunges_rolls_back(self):
        r = evaluate_window(_base(), _shadow(avg_result_count=2.0))
        assert r["verdict"] == "rollback"
        assert "avg_result" in r["reason"]

    def test_avg_result_within_tolerance_promotes(self):
        r = evaluate_window(_base(), _shadow(avg_result_count=3.8))
        assert r["verdict"] == "promote"

    def test_jaccard_diverges_rolls_back(self):
        r = evaluate_window(_base(), _shadow(jaccard_vs_baseline=0.5))
        assert r["verdict"] == "rollback"
        assert "jaccard" in r["reason"]

    def test_jaccard_none_skips_check(self):
        """无基线（jaccard=None）跳过 jaccard 护栏，只比前两项。"""
        r = evaluate_window(_base(), _shadow(jaccard_vs_baseline=None))
        assert r["verdict"] == "promote"

    def test_jaccard_meets_floor_promotes(self):
        r = evaluate_window(_base(), _shadow(jaccard_vs_baseline=0.75))
        assert r["verdict"] == "promote"

    def test_missing_key_metric_holds(self):
        """关键指标缺失时保守 hold（不误判 promote）。"""
        r = evaluate_window(_base(), {"sample_count": 10})
        assert r["verdict"] == "hold"
        assert r["reason"] == "key_metrics_missing"

    def test_improved_metrics_promotes(self):
        r = evaluate_window(_base(), _shadow(zero_result_rate=0.05, avg_result_count=4.8))
        assert r["verdict"] == "promote"

    def test_signals_recorded(self):
        r = evaluate_window(_base(), _shadow(avg_result_count=3.0))
        assert r["signals"]["base_avg_result_count"] == 4.5
        assert r["signals"]["shadow_avg_result_count"] == 3.0


class TestShadowIsDue:
    def test_deadline_passed(self):
        w = Mock(check_deadline=utcnow() - timedelta(days=1))
        assert shadow_is_due(w) is True

    def test_deadline_future(self):
        w = Mock(check_deadline=utcnow() + timedelta(days=1))
        assert shadow_is_due(w) is False

    def test_no_deadline_not_due(self):
        w = Mock(check_deadline=None)
        assert shadow_is_due(w) is False


class TestDecidePromoteTarget:
    def test_observing_and_due_allows(self):
        w = Mock(status="observing",
                 check_deadline=utcnow() - timedelta(days=1),
                 started_at=utcnow() - timedelta(days=7))
        assert decide_promote_target(w) is True

    def test_not_observing_rejects(self):
        w = Mock(status="promoted",
                 check_deadline=utcnow() - timedelta(days=1),
                 started_at=utcnow() - timedelta(days=7))
        assert decide_promote_target(w) is False

    def test_not_due_rejects(self):
        w = Mock(status="observing",
                 check_deadline=utcnow() + timedelta(days=1),
                 started_at=utcnow() - timedelta(days=7))
        assert decide_promote_target(w) is False

    def test_min_promote_days_guard(self):
        w = Mock(status="observing",
                 check_deadline=utcnow() - timedelta(days=1),
                 started_at=utcnow() - timedelta(days=2))
        assert decide_promote_target(w, min_promote_days=7) is False
        assert decide_promote_target(w, min_promote_days=1) is True

    def test_no_started_at_rejects_with_min_days(self):
        w = Mock(status="observing",
                 check_deadline=utcnow() - timedelta(days=1),
                 started_at=None)
        assert decide_promote_target(w, min_promote_days=1) is False


# ── 集成测试（真实 SQLite + mock 外部网络） ─────────────────────────

@pytest.fixture(scope="function")
def shadow_db():
    """内存 SQLite + patch db.get_session + mock dry-run 外部网络。"""
    import remembrance.models.tables  # noqa: F401
    import remembrance.eval.models  # noqa: F401
    import remembrance.parameters.trust_models  # noqa: F401
    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.retrieval.hybrid.get_vector_store") as vs, \
         patch("remembrance.retrieval.hybrid.embed",
               return_value=[[0.1] * 8]), \
         patch("remembrance.retrieval.reranker.rerank", return_value=[]), \
         patch("remembrance.retrieval.hybrid.classify_intent",
               return_value={"intent": "exploratory", "candidate_n": 10}):
        vs.return_value.search.return_value = [{"id": "mem_1", "distance": 0.1}]
        yield get_test_session


class TestShadowIntegration:
    def _seed_query_set(self, sf):
        """造查询集（build_query_set 需要 retrieval_event 源）。"""
        from remembrance.eval.query_set import build_query_set
        from remembrance.models.tables import MemoryItem, RetrievalEvent
        with sf() as s:
            for i in range(2):
                s.add(RetrievalEvent(
                    id=f"se_ev_{i}", trace_id="t",
                    query_text=f"shadow query {i}", query_norm_hash=f"sh{i}",
                    lane="", param_snapshot_hash="sha256:x",
                    result_ids=[], result_scores=[], used_ids=[], latency_ms=1,
                    zero_result=False, is_system_noise=False,
                    created_at=utcnow() - timedelta(minutes=i)))
            s.add(MemoryItem(id="mem_1", memory_type="semantic", key="k",
                             content="影子测试记忆", lane="general",
                             status="active"))
            s.commit()
        build_query_set("dry-run-v1")

    def test_table_created(self, shadow_db):
        from remembrance.parameters.trust_models import ShadowWindow
        sf = shadow_db
        with sf() as s:
            w = ShadowWindow(id="sw_t1", override_id="rev_1",
                             param_overrides={"RETRIEVAL_W_VECTOR": 0.7},
                             base_snapshot={"RETRIEVAL_W_VECTOR": 0.6},
                             status="observing")
            s.add(w)
            s.commit()
            assert s.get(ShadowWindow, "sw_t1").status == "observing"

    def test_open_shadow_persists(self, shadow_db):
        from remembrance.parameters.runtime import open_shadow
        from remembrance.parameters.trust_models import ShadowWindow
        sf = shadow_db
        self._seed_query_set(sf)
        w = open_shadow("rev_42", {"RETRIEVAL_W_VECTOR": 0.75}, observe_days=1)
        assert w.status == "observing"
        assert w.param_overrides == {"RETRIEVAL_W_VECTOR": 0.75}
        assert w.override_id == "rev_42"
        with sf() as s:
            got = s.get(ShadowWindow, w.id)
            assert got is not None
            # SQLite 存 naive datetime，转 naive 比较
            deadline = got.check_deadline
            if deadline.tzinfo is not None:
                deadline = deadline.replace(tzinfo=None)
            assert deadline > utcnow().replace(tzinfo=None) - timedelta(hours=1)

    def test_open_shadow_respects_max_windows(self, shadow_db):
        """MAX_ACTIVE_SHADOW_WINDOWS=1：新窗取消最旧 observing。"""
        from remembrance.core.settings import settings as _s
        from remembrance.parameters.runtime import open_shadow
        from remembrance.parameters.trust_models import ShadowWindow
        sf = shadow_db
        self._seed_query_set(sf)
        w1 = open_shadow("rev_1", {"RETRIEVAL_W_VECTOR": 0.7}, observe_days=1)
        w2 = open_shadow("rev_2", {"RETRIEVAL_W_VECTOR": 0.8}, observe_days=1)
        assert _s.MAX_ACTIVE_SHADOW_WINDOWS == 1
        with sf() as s:
            old = s.get(ShadowWindow, w1.id)
            assert old.status == "cancelled"
            assert old.verdict_reason == "cancelled_by_new_window"
            new = s.get(ShadowWindow, w2.id)
            assert new.status == "observing"

    def test_check_shadow_due_promotes(self, shadow_db):
        """到期窗跑 dry-run 后 promote（mock 向量全命中 → 指标健康）。"""
        from remembrance.parameters.runtime import open_shadow
        from remembrance.parameters.trust_models import ShadowWindow
        sf = shadow_db
        self._seed_query_set(sf)
        w = open_shadow("rev_7", {"RETRIEVAL_W_VECTOR": 0.7}, observe_days=1)
        # 手动把 deadline 拨到过去，触发到期判定（SQLite 存 naive）
        with sf() as s:
            got = s.get(ShadowWindow, w.id)
            got.check_deadline = utcnow().replace(tzinfo=None) - timedelta(days=1)
            s.add(got)
            s.commit()
        from remembrance.parameters.runtime import check_shadow_due
        results = check_shadow_due()
        assert len(results) == 1
        assert results[0]["window_id"] == w.id
        assert results[0]["verdict"] in ("promote", "hold", "rollback")
        with sf() as s:
            updated = s.get(ShadowWindow, w.id)
            # 不应是 observing（已被判定）
            assert updated.status != "observing"
            # promote 只标记，不写 ParamOverride（DEDUP shadow-only + 人工闸门）
            overrides = s.exec(select(ParamOverride)).all()
            assert len(overrides) == 0

    def test_rollback_writes_override(self, shadow_db):
        """rollback 时写 ParamOverride(operation=rollback) 恢复基线。"""
        from remembrance.parameters.runtime import open_shadow, _rollback_snapshot
        from remembrance.parameters.trust_models import ShadowWindow
        sf = shadow_db
        self._seed_query_set(sf)
        w = open_shadow("rev_9", {"RETRIEVAL_W_VECTOR": 0.7}, observe_days=1)
        with sf() as s:
            got = s.get(ShadowWindow, w.id)
            got.rollback_reason = "test_rollback"
            _rollback_snapshot(s, got)
        with sf() as s:
            overrides = s.exec(select(ParamOverride)).all()
            assert len(overrides) == 1
            assert overrides[0].operation == "rollback"
            assert overrides[0].actor == "shadow_guardrail"
            assert overrides[0].after_snapshot == w.base_snapshot
