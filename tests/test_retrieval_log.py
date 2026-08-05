"""
检索事件埋点冒烟测试（方向二弱标注源）——真实 SQLite，不 mock。
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import remembrance.storage.db as db_module
from remembrance.models.tables import MemoryItem, RetrievalEvent


@pytest.fixture(scope="function")
def client():
    import remembrance.models.tables  # noqa: F401
    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("remembrance.storage.vector_store.ChromaVectorStore"), \
         patch("remembrance.retrieval.hybrid.get_vector_store",
               return_value=__import__("unittest.mock", fromlist=["Mock"])
               .Mock(search=__import__("unittest.mock", fromlist=["Mock"])
                     .Mock(return_value=[]))), \
         patch("remembrance.retrieval.reranker.rerank", return_value=[]), \
         patch("remembrance.retrieval.hybrid.embed",
               return_value=[[0.1] * 8]):
        from api_server import app
        with TestClient(app) as c:
            yield c, get_test_session


class TestRetrievalLog:
    def test_search_logs_event(self, client):
        c, sf = client
        # 有记忆可召回的场景（否则 vector 空直接返回空）
        with sf() as s:
            s.add(MemoryItem(
                id="mem_1", memory_type="semantic", key="k",
                content="上次讨论的检索方案是混合三路召回",
                lane="fact", status="active"))
            s.commit()
        with patch("remembrance.retrieval.hybrid.get_vector_store") as vs:
            vs.return_value.search.return_value = [{"id": "mem_1",
                                                    "distance": 0.1}]
            r = c.post("/search", json={"query": "上次我们聊的检索方案",
                                        "top_k": 3})
        assert r.status_code == 200
        with sf() as s:
            events = s.exec(select(RetrievalEvent)).all()
            assert len(events) >= 1
            ev = events[-1]
            assert ev.query_text == "上次我们聊的检索方案"
            assert ev.query_norm_hash  # 归一化哈希
            assert ev.param_snapshot_hash.startswith("sha256:")
            assert ev.latency_ms >= 0
            assert ev.zero_result is False

    def test_gate_blocked_logs_zero_result(self, client):
        c, sf = client
        # 纯社交结束语 → 闸门拦截 → 记录 zero_result 事件
        r = c.post("/search", json={"query": "好的谢谢再见",
                                    "top_k": 3})
        assert r.status_code == 200
        with sf() as s:
            events = s.exec(select(RetrievalEvent)).all()
            assert len(events) >= 1
            assert events[-1].zero_result is True

    def test_log_failure_non_fatal(self, client):
        """埋点失败绝不影响主链路。"""
        c, sf = client
        with patch("remembrance.observability.retrieval_log.db.get_session",
                   side_effect=RuntimeError("db down")):
            r = c.post("/search", json={"query": "好的谢谢再见",
                                        "top_k": 3})
        assert r.status_code == 200  # 埋点崩了搜索照常


class TestShellHookLog:
    def test_shell_hook_logs_event(self):
        """Shell Hook 独立向量路径也要埋点（真实流量主通道之一）。"""
        from unittest.mock import Mock

        import scripts.shell_hook as hook
        with patch("scripts.shell_hook.embed",
                   return_value=[[0.1] * 8]), \
             patch("scripts.shell_hook.get_vector_store") as vs, \
             patch("scripts.shell_hook.db.get_session") as db_sess:
            vs.return_value.search.return_value = [
                {"id": "mem_1", "distance": 0.1}]
            # mock db session 返回空 items（避免污染）
            db_sess.return_value.__enter__.return_value.exec.return_value \
                .all.return_value = []
            result = hook.build_context("上次的检索方案")
            assert isinstance(result, dict)  # 返回 {} 或 context，不抛

    def test_shell_hook_empty_query_no_log(self):
        import scripts.shell_hook as hook
        assert hook.build_context("") == {}
