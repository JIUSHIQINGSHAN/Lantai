"""评估运行器冒烟测试——mock 外部网络（embed/vector_store），真实 SQLite。

覆盖：run_dry_run 落库、metrics 有值、单条失败不中断、param_overrides 生效、
baseline jaccard、load/list。
"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import remembrance.storage.db as db_module
from remembrance.models.tables import MemoryItem, RetrievalEvent
from remembrance.eval.models import EvalQuerySet, EvalRun
from remembrance.eval.query_set import build_query_set
from remembrance.eval.runner import list_runs, load_run, run_dry_run


@pytest.fixture(scope="function")
def db_session():
    """内存 SQLite + patch db.get_session + 外部网络 mock。"""
    import remembrance.models.tables  # noqa: F401
    import remembrance.eval.models  # noqa: F401
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


def _seed(db_session, events=2):
    """造几条检索事件 + 一条记忆，返回查询集。"""
    import datetime
    from remembrance.core.time import utcnow
    with db_session() as s:
        base = utcnow() - datetime.timedelta(minutes=events)
        for i in range(events):
            s.add(RetrievalEvent(
                id=f"ev_{i}", trace_id="t", query_text=f"query number {i}",
                query_norm_hash=f"h{i}", lane="", param_snapshot_hash="sha256:x",
                result_ids=[], result_scores=[], used_ids=[], latency_ms=1,
                zero_result=False, is_system_noise=False,
                created_at=base + datetime.timedelta(minutes=i)))
        s.add(MemoryItem(
            id="mem_1", memory_type="semantic", key="k",
            content="测试记忆内容", lane="general", status="active"))
        s.commit()
    return build_query_set("dry-run-test")


class TestRunDryRun:
    def test_run_creates_evalrun(self, db_session):
        qs = _seed(db_session)
        run = run_dry_run(qs, top_k=3)
        assert run.status == "done"
        assert run.query_set_name == "dry-run-test"
        assert run.finished_at is not None
        assert run.metrics["sample_count"] == 2
        assert run.metrics["zero_result_rate"] == 0.0  # 都命中 mem_1
        assert run.metrics["avg_result_count"] == 1.0
        # 落库可查
        with db_session() as s:
            assert s.get(EvalRun, run.id) is not None

    def test_per_query_collected(self, db_session):
        qs = _seed(db_session)
        run = run_dry_run(qs, top_k=3)
        assert len(run.per_query) == 2
        pq = run.per_query[0]
        # build_query_set 按 created_at 降序（最新优先），ev_1 最新在前
        assert pq["query"] == "query number 1"
        assert pq["result_ids"] == ["mem_1"]
        assert pq["zero_result"] is False
        assert pq["latency_ms"] >= 0

    def test_param_overrides_recorded(self, db_session):
        qs = _seed(db_session)
        run = run_dry_run(qs, param_overrides={"RETRIEVAL_W_VECTOR": 0.7})
        assert run.param_overrides == {"RETRIEVAL_W_VECTOR": 0.7}
        # 快照合并后包含覆盖值
        assert run.param_snapshot["RETRIEVAL_W_VECTOR"] == 0.7

    def test_single_query_failure_not_fatal(self, db_session):
        """单条查询抛错不中断，metrics.errors 计数。"""
        qs = _seed(db_session)
        with patch("remembrance.eval.runner.hybrid_search",
                   side_effect=[RuntimeError("embed timeout"), []]):
            run = run_dry_run(qs, top_k=3)
        assert run.status == "done"
        assert run.metrics["errors"] == 1
        assert run.per_query[0].get("error")
        assert run.per_query[1]["zero_result"] is True

    def test_baseline_jaccard(self, db_session):
        qs = _seed(db_session)
        base = run_dry_run(qs, top_k=3)
        run2 = run_dry_run(qs, top_k=3, baseline_run_id=base.id)
        assert run2.metrics["jaccard_vs_baseline"] == 1.0  # 同 mock 完全一致

    def test_load_and_list(self, db_session):
        qs = _seed(db_session)
        run = run_dry_run(qs)
        assert load_run(run.id).id == run.id
        runs = list_runs(query_set_name="dry-run-test")
        assert len(runs) >= 1
        assert runs[0].id == run.id


class TestParamOverrideContext:
    def test_override_restores_settings(self):
        """param_overrides 调用后 settings 恢复原值（不污染全局）。"""
        from remembrance.core.settings import settings
        from remembrance.retrieval.hybrid import _param_override
        original = settings.RETRIEVAL_W_VECTOR
        with _param_override({"RETRIEVAL_W_VECTOR": 0.99}):
            assert settings.RETRIEVAL_W_VECTOR == 0.99
        assert settings.RETRIEVAL_W_VECTOR == original

    def test_none_override_noop(self):
        from remembrance.retrieval.hybrid import _param_override
        with _param_override(None):
            pass  # 不抛即通过
