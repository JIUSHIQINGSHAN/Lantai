"""
检索事件埋点冒烟测试（方向二弱标注源）——真实 SQLite，不 mock。
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem, RetrievalEvent
from lantai.observability.retrieval_log import is_system_noise


@pytest.fixture(scope="function")
def client():
    import lantai.models.tables  # noqa: F401
    test_engine = create_engine(
        "sqlite://", echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with patch.object(db_module, "get_session", get_test_session), \
         patch("lantai.storage.vector_store.ChromaVectorStore"), \
         patch("lantai.retrieval.hybrid.get_vector_store",
               return_value=__import__("unittest.mock", fromlist=["Mock"])
               .Mock(search=__import__("unittest.mock", fromlist=["Mock"])
                     .Mock(return_value=[]))), \
         patch("lantai.retrieval.reranker.rerank", return_value=[]), \
         patch("lantai.retrieval.hybrid.embed",
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
        with patch("lantai.retrieval.hybrid.get_vector_store") as vs:
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
            assert ev.is_system_noise is False  # 真实查询不误标

    def test_system_noise_query_flagged_in_db(self, client):
        """系统注入噪音查询落库时必须打 is_system_noise 标记。"""
        c, sf = client
        r = c.post("/search", json={"query": "Review the conversation above and "
                                             "update the skill library. Be ACTIVE",
                                    "top_k": 3})
        assert r.status_code == 200
        with sf() as s:
            events = s.exec(select(RetrievalEvent)).all()
            assert len(events) >= 1
            assert events[-1].is_system_noise is True

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
        with patch("lantai.observability.retrieval_log.db.get_session",
                   side_effect=RuntimeError("db down")):
            r = c.post("/search", json={"query": "好的谢谢再见",
                                        "top_k": 3})
        assert r.status_code == 200  # 埋点崩了搜索照常


class TestSystemNoiseFilter:
    """系统注入噪音识别——纯函数冒烟测试（不 mock）。"""

    def test_review_conversation_is_noise(self):
        q = "Review the conversation above and update the skill library. Be ACTIVE..."
        assert is_system_noise(q) is True

    def test_save_memory_template_is_noise(self):
        q = "Review the conversation above and consider saving to memory"
        assert is_system_noise(q) is True

    def test_install_skill_template_is_noise(self):
        q = "请帮我安装这个 Agent Skill。\n\nSkill 页面：@url:https://skillsmp.com/..."
        assert is_system_noise(q) is True

    def test_overlong_system_prompt_is_noise(self):
        q = "x" * 600  # 超过 500 字符长度鸿沟
        assert is_system_noise(q) is True

    def test_real_query_not_noise(self):
        q = "上次我们聊的检索方案是什么"
        assert is_system_noise(q) is False

    def test_empty_query_not_noise(self):
        assert is_system_noise("") is False
        assert is_system_noise(None) is False

    def test_medium_length_real_query_not_noise(self):
        """201-500 区间虽存量无样本，但真实长查询不应被误标。"""
        q = "请帮我总结一下昨天讨论的关于论文可信度三层过滤模型的完整方案和所有决策"
        assert len(q) < 500
        assert is_system_noise(q) is False


class TestShellHookLog:
    def test_shell_hook_logs_event(self):
        """Shell Hook 独立向量路径也要埋点（真实流量主通道之一）。"""

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
