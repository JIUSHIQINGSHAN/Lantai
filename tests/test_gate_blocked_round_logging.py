"""gate 拦截轮落库脱文（v0.68 票 11 / 巡检立案轮 2）。

ADR-0084 §2.5 边界表述「gate 判 needs_memory=False 不落事件」只对 shell_hook
成立；REST/MCP 的拦截分支照落原话（query_text/query_norm_hash 带原文）。
工单修复目标：拦截轮**保留落事件**（zero_result 遥测价值）但**写入时即空原话
两列**；is_system_noise/estimated_tokens 等标记以原查询在抹除前照算。

测试不 mock 被测函数：真内存库、真 gate（relevance_check 现算，先清热缓存——
票 08 实测追问沿用跨测试串味教训）、真 REST TestClient / 真 MCP handler /
真 build_context。db.get_session 与向量库 stub 属测试装配（同
test_retrieval_log 惯例），不是被测链路的 mock。
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.gate.prefilter as prefilter
import lantai.storage.db as db_module
from lantai.models.tables import RetrievalEvent

# 三入口共用的真实拦截句（test_retrieval_log 既有锁同款，实证被真 gate 拦截）
BLOCKED_QUERY = "好的谢谢再见"
# >500 字符的已知系统噪音形状（is_system_noise 的确定性判据，见 retrieval_log.py）
NOISE_QUERY = "Review the conversation above and update the skill library. " + "长" * 260


@pytest.fixture()
def mem_db(monkeypatch):
    """真内存库（StaticPool 单连接）+ db.get_session 装配 + gate 热缓存复位。"""
    import lantai.models.tables  # noqa: F401

    test_engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    monkeypatch.setattr(db_module, "get_session", get_test_session)
    monkeypatch.setattr(
        prefilter,
        "_LAST_GATE_DECISION",
        {"time": 0.0, "query": "", "needs_memory": False},
    )
    monkeypatch.setattr(prefilter, "_USER_GATE_DECISIONS", {})
    yield get_test_session


class TestRestBlockedRound:
    def test_blocked_round_event_sanitized_at_write(self, mem_db):
        """红测 A：REST 拦截轮照落事件，但原话两列必须写入即空。"""
        with (
            patch("lantai.storage.vector_store.ChromaVectorStore"),
            patch("lantai.retrieval.hybrid.get_vector_store"),
            patch("lantai.retrieval.reranker.rerank", return_value=[]),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        ):
            from lantai.api.app import app

            with TestClient(app) as c:
                r = c.post("/search", json={"query": BLOCKED_QUERY, "top_k": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["gate"]["needs_memory"] is False
        event_id = body["event_id"]
        assert event_id, "拦截轮必须落事件（zero_result 遥测载体，票 11 修复目标）"
        with mem_db() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev is not None, "event_id 指向的事件必须真实落库"
            assert ev.receipt_status == "pending"
            assert ev.query_text == "", f"拦截轮原话不得落库：{ev.query_text!r}"
            assert ev.query_norm_hash == "", "归一化哈希对低熵原文是可验证口令，同抹"
            assert ev.zero_result is True, "拦截轮的 zero_result 遥测价值必须保留"


class TestMarkersBeforeScrub:
    def test_persist_query_false_scrubs_text_keeps_markers(self, mem_db):
        """log_retrieval 脱文形态：标记以原查询照算，原话两列为空。

        实现陷阱对照：调用方直接传 query="" 会让 is_system_noise("")=False、
        tokens 归零（retrieval_log.py:44-50）——违反工单「抹除前照算」。
        """
        from lantai.observability.retrieval_log import log_retrieval

        event_id = log_retrieval(NOISE_QUERY, [], latency_ms=1, persist_query=False)
        assert event_id
        with mem_db() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev.query_text == ""
            assert ev.query_norm_hash == ""
            assert ev.is_system_noise is True, "is_system_noise 必须以原查询照算"
            assert ev.estimated_tokens > 0, "estimated_tokens 必须以原查询照算"
            assert ev.zero_result is True


class TestMcpBlockedRound:
    def test_blocked_round_event_sanitized_at_write(self, mem_db):
        """红测 B：MCP handle_search 拦截轮同口径（真 gate，不 patch relevance_check）。"""
        with patch("lantai.storage.vector_store.ChromaVectorStore"):
            spec = importlib.util.spec_from_file_location(
                "mcp_server_t11",
                Path(__file__).parent.parent / "lantai" / "cli" / "mcp.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        out = mod.handle_search({"query": BLOCKED_QUERY})
        assert out["gate"]["needs_memory"] is False
        event_id = out["event_id"]
        assert event_id, "MCP 拦截轮必须落事件"
        with mem_db() as s:
            ev = s.get(RetrievalEvent, event_id)
            assert ev is not None
            assert ev.receipt_status == "pending"
            assert ev.query_text == "", f"MCP 拦截轮原话不得落库：{ev.query_text!r}"
            assert ev.query_norm_hash == ""
            assert ev.zero_result is True


class TestShellHookControlArm:
    def test_blocked_round_still_does_not_log(self, mem_db):
        """对照臂：shell_hook 拦截轮仍不落事件（工单要求行为不变）。"""
        spec = importlib.util.spec_from_file_location(
            "shell_hook_t11",
            Path(__file__).parent.parent / "scripts" / "shell_hook.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with mem_db() as s:
            before = len(s.exec(select(RetrievalEvent)).all())
        ctx = mod.build_context(BLOCKED_QUERY, session_id="s-ctl")
        assert isinstance(ctx, dict)
        assert "event_id" not in ctx
        with mem_db() as s:
            after = len(s.exec(select(RetrievalEvent)).all())
        assert after == before, "shell_hook 拦截轮不落事件（现状即绿对照臂）"
