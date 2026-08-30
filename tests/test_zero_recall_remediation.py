"""拾遗（ADR-0028）：零召回根因治理与检索韧性降级测试。

验证：
1. hybrid_search 在 embed() 报错（网络超时/401鉴权失败）时不抛异常，平滑降级至本地 FTS5+BM25 关键词检索。
2. prefilter.relevance_check 识别「大哥」自指及常见实词短查询，不再被武断拦截。
3. routes_search 在 force=True 或有效实词短查询时不被盲目阻断。
"""
from datetime import datetime, timezone
import pytest
from sqlmodel import Session

from lantai.models.tables import MemoryItem
from lantai.gate.prefilter import relevance_check
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage.fts import index_fts, init_fts


@pytest.fixture
def clean_gate_cache():
    """确保单测之间 gate cache 隔离"""
    return {"time": 0.0, "query": "", "needs_memory": False}


class TestPrefilterCalibration:
    """测试相关性闸门校准（ADR-0028）：覆盖常用短查询与自指。"""

    def test_dage_self_reference_detected(self, clean_gate_cache):
        res = relevance_check("大哥电脑配置", cache=clean_gate_cache)
        assert res["needs_memory"] is True
        assert res["reason"] == "self_reference"

    def test_short_content_query_with_substantive_words(self, clean_gate_cache):
        res = relevance_check("什么是事件驱动架构", cache=clean_gate_cache)
        assert res["needs_memory"] is True
        assert res["reason"] == "content_query"

    def test_short_technical_query(self, clean_gate_cache):
        res = relevance_check("华硕天选三显卡", cache=clean_gate_cache)
        assert res["needs_memory"] is True

    def test_social_closer_still_blocked(self, clean_gate_cache):
        res = relevance_check("好的谢谢", cache=clean_gate_cache)
        assert res["needs_memory"] is False
        assert res["reason"] == "social_closer"

    def test_empty_or_too_short_blocked(self, clean_gate_cache):
        res = relevance_check("嗯", cache=clean_gate_cache)
        assert res["needs_memory"] is False


class TestHybridSearchShiyiFallback:
    """测试拾遗多级降级（ADR-0028）：向量异常平滑降级至 FTS+BM25。"""

    def test_embedding_failure_smoothly_falls_back_to_keyword(self, param_env, monkeypatch):
        """核心函数不 mock 冒烟：真实 SQLite 库中存入记忆，模拟 embed() 抛 401，验证通过 FTS5 召回。"""
        session_factory, engine = param_env
        init_fts(engine.raw_connection())

        # 写入真实记忆
        item = MemoryItem(
            id="mem_test_shiyi_01",
            key="test-key-01",
            memory_type="text",
            content="大哥的电脑配置为华硕天选三，RTX 3050显卡。",
            lane="fact",
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            decay_score=1.0,
            importance=0.8,
        )
        with session_factory() as s:
            s.add(item)
            s.commit()
            raw_conn = s.connection().connection.driver_connection
            index_fts(raw_conn, item.id, item.content)

        # 模拟外部 Embedding API 鉴权失效（401 Unauthorized）
        def mock_embed_fail(texts):
            raise RuntimeError("401 Token is invalid")

        monkeypatch.setattr("lantai.retrieval.hybrid.embed", mock_embed_fail)

        # 执行混合检索，验证不挂死且正确降级召回
        results = hybrid_search("华硕天选三", top_k=5, use_rerank=False)
        assert len(results) > 0
        hit_ids = [r["memory"]["id"] for r in results]
        assert "mem_test_shiyi_01" in hit_ids
        assert results[0]["memory"]["content"] == item.content

    def test_embedding_failure_with_trace(self, param_env, monkeypatch):
        """trace=True 时，记录降级标记。"""
        session_factory, engine = param_env
        init_fts(engine.raw_connection())

        item = MemoryItem(
            id="mem_test_shiyi_02",
            key="test-key-02",
            memory_type="text",
            content="飞书卡片审美要求使用古诗词点缀。",
            lane="preference",
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            decay_score=1.0,
            importance=0.8,
        )
        with session_factory() as s:
            s.add(item)
            s.commit()
            raw_conn = s.connection().connection.driver_connection
            index_fts(raw_conn, item.id, item.content)

        monkeypatch.setattr("lantai.retrieval.hybrid.embed",
                            lambda texts: (_ for _ in ()).throw(ConnectionError("Network down")))

        results, trace_steps = hybrid_search("古诗词", top_k=5, use_rerank=False, trace=True)
        assert len(results) > 0
        step_names = [t["step"] for t in trace_steps]
        assert "fallback_fts" in step_names or "final" in step_names


class TestRoutesSearchForce:
    """测试 /search 端点在 force=True 时绕过闸门透传。"""

    def test_search_with_force_bypasses_gate(self, param_env, monkeypatch):
        from fastapi.testclient import TestClient
        from api_server import app

        session_factory, engine = param_env
        init_fts(engine.raw_connection())

        item = MemoryItem(
            id="mem_force_01",
            key="test-force-key",
            memory_type="text",
            content="这是一条用于测试强制检索透传的知识记录。",
            lane="general",
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            decay_score=1.0,
            importance=0.8,
        )
        with session_factory() as s:
            s.add(item)
            s.commit()
            raw_conn = s.connection().connection.driver_connection
            index_fts(raw_conn, item.id, item.content)

        client = TestClient(app)
        # force=False 且输入未匹配信号时被闸门拦截
        resp1 = client.post("/search", json={"query": "测试", "force": False})
        assert resp1.status_code == 200
        assert resp1.json()["gate"]["needs_memory"] is False
        assert len(resp1.json()["results"]) == 0

        # force=True 时绕过闸门，进入检索链路并召回
        resp2 = client.post("/search", json={"query": "测试", "force": True})
        assert resp2.status_code == 200
        assert len(resp2.json()["results"]) > 0
        assert resp2.json()["results"][0]["memory"]["id"] == "mem_force_01"

