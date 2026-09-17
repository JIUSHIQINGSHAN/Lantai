"""Ticket 01: Dialogue Ingest 对话写通道

核心冒烟测试（真实内存 SQLite；仅 mock 外部 LLM chat_json）：
- fastpath 直通（"记住：X" / 自我声明 / 偏好表达）
- 闲聊 → 候选进 pending_review（不静默丢弃、不落库为记忆）
- 偏好/事实 → 候选 lane 正确
- LLM 提取失败（上游异常）→ 兜底候选入队，不抛错
- 低置信度提取 → pending_review
- REST：POST /dialogue
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryCandidate, RawDocument


class TestDialogueIngest:
    """对话写通道核心函数"""

    def test_fastpath_explicit_instruction_direct(self, param_env):
        """'记住：X' → fastpath 直通（绕过 LLM 提取）"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            side_effect=AssertionError("fastpath 不应触发 LLM 提取"),
        ):
            result = ingest_dialogue("记住：明天下午3点开会")
        assert result["ingested"] is True
        assert result["fastpath"] is True
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "fastpath"
            assert cand.lane == "general"
            assert cand.extractor_confidence == 1.0

    def test_fastpath_self_declaration_lane(self, param_env):
        """'我是后端工程师' → fastpath，lane=fact"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            side_effect=AssertionError("fastpath 不应触发 LLM 提取"),
        ):
            result = ingest_dialogue("我是后端工程师")
        assert result["fastpath"] is True
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "fastpath"
            assert cand.lane == "fact"

    def test_preference_text_lane(self, param_env):
        """含偏好表达的长文本 → LLM 提取，lane=preference"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            return_value={
                "summary": "用户偏好 Rust",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": ["rust"],
                "extractor_confidence": 0.8,
            },
        ):
            result = ingest_dialogue("我最近特别喜欢用 Rust 写 CLI 工具，感觉很顺手")
        assert result["ingested"] is True
        assert result["fastpath"] is False
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "new"
            assert cand.lane == "preference"

    def test_chitchat_enters_pending_review(self, param_env):
        """闲聊（短文本/社交结束语）→ 沙汰直接 rejected，不落库为记忆（ADR-0026）"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            side_effect=AssertionError("闲聊不应触发 LLM 提取"),
        ):
            result = ingest_dialogue("哈哈，好的")
        assert result["ingested"] is True
        assert result["status"] == "rejected"
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "rejected"
            assert s.exec(select(RawDocument)).all()  # rawdocument 仍建（可追溯）

    def test_extraction_failure_falls_back_to_queue(self, param_env):
        """LLM 提取抛异常 → 兜底候选进 pending_review，不抛错"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch("lantai.parsing.extractor.chat_json", side_effect=RuntimeError("upstream 502")):
            result = ingest_dialogue("我在研究知识图谱的记忆架构，感觉很有意思")
        assert result["ingested"] is True
        assert result["status"] == "pending_review"
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "pending_review"

    def test_low_confidence_enters_pending_review(self, param_env):
        """提取置信度过低 → 待审队列（不静默丢弃）"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            return_value={
                "summary": "杂音",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": [],
                "extractor_confidence": 0.2,
            },
        ):
            result = ingest_dialogue("今天天气不错，不过也没什么特别的")
        assert result["status"] == "pending_review"
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.status == "pending_review"

    def test_user_id_and_source_recorded(self, param_env):
        """对话来源与用户 id 落 RawDocument.meta / source_type"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            return_value={
                "summary": "s",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": [],
                "extractor_confidence": 0.8,
            },
        ):
            result = ingest_dialogue("我喜欢在早上写代码", user_id="u_42", source="hermes")
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            doc = s.get(RawDocument, cand.document_id)
            assert doc.source_type == "dialogue"
            assert doc.meta.get("user_id") == "u_42"
            assert doc.meta.get("source") == "hermes"

    def test_empty_text_rejected(self, param_env):
        from lantai.ingestion.dialogue import ingest_dialogue

        with pytest.raises(ValueError):
            ingest_dialogue("   ")


class TestSessionOriginChain:
    """v022 吸收票据 01：来源链贯通——session_id / origin_turn 显式落写入路径。

    上游 aiduMEI v21.2 教训：写入路径不透传 session，回声抑制/会话萃取/
    轨迹登记三个功能上线即空转而探针全绿。"""


    def test_session_and_turn_recorded_on_candidate(self, param_env):
        """带 session_id/turn 的对话摄取 → 候选列与 provenance 双落值"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            side_effect=AssertionError("fastpath 不应触发 LLM 提取"),
        ):
            result = ingest_dialogue(
                "记住：明天下午3点开会",
                user_id="u_7",
                session_id="sess_abc",
                turn=3,
            )
        assert result["ingested"] is True
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.session_id == "sess_abc"
            assert cand.user_id == "u_7"
            assert cand.provenance.get("origin_session_id") == "sess_abc"
            assert cand.provenance.get("origin_turn") == 3
            assert cand.provenance.get("origin_source") == "dialogue"


    def test_no_session_stays_null(self, param_env):
        """无 session 的写入如实留空（NULL），不猜测、不编造"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            side_effect=AssertionError("fastpath 不应触发 LLM 提取"),
        ):
            result = ingest_dialogue("记住：后天上午10点体检")
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.session_id is None
            assert "origin_session_id" not in cand.provenance
            assert "origin_turn" not in cand.provenance


    def test_session_recorded_on_extraction_path(self, param_env):
        """LLM 提取路径同样落 session（非 fastpath 专用）"""
        session_factory, _ = param_env
        from lantai.ingestion.dialogue import ingest_dialogue

        with patch(
            "lantai.parsing.extractor.chat_json",
            return_value={
                "summary": "s",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": [],
                "extractor_confidence": 0.9,
            },
        ):
            result = ingest_dialogue(
                "我们团队决定下周开始全面转向新架构", user_id="u_7", session_id="sess_ext"
            )
        assert result["fastpath"] is False
        with session_factory() as s:
            cand = s.get(MemoryCandidate, result["candidate_id"])
            assert cand.session_id == "sess_ext"
            assert cand.provenance.get("origin_session_id") == "sess_ext"


    def test_dialogue_route_passes_session(self, client):
        """REST POST /dialogue 透传 session_id / turn"""
        import lantai.storage.db as db_mod

        resp = client.post(
            "/dialogue",
            json={"text": "记住：周五下午发布新版", "session_id": "sess_http", "turn": 5},
        )
        assert resp.status_code == 200
        cand_id = resp.json()["candidate_id"]
        with db_mod.get_session() as s:
            cand = s.get(MemoryCandidate, cand_id)
            assert cand.session_id == "sess_http"
            assert cand.provenance.get("origin_turn") == 5


# ── REST 路由测试 ──────────────────────────────────────────────


@pytest.fixture(scope="function")
def client():
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        return Session(test_engine)

    with (
        patch.object(db_module, "get_session", get_test_session),
        patch(
            "lantai.retrieval.intent.chat_json",
            return_value={"intent": "fact_lookup", "reason": "test"},
        ),
        patch(
            "lantai.parsing.extractor.chat_json",
            return_value={
                "summary": "test",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "topic": [],
                "extractor_confidence": 0.8,
            },
        ),
        patch("lantai.retrieval.reranker.rerank", return_value=[]),
        patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store"),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
    ):
        from lantai.api.app import app

        with TestClient(app) as c:
            yield c


class TestDialogueRoute:
    """REST POST /dialogue"""

    def test_fastpath_direct(self, client):
        resp = client.post("/dialogue", json={"text": "记住：明天下午3点开会"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] is True
        assert data["fastpath"] is True

    def test_chitchat_pending(self, client):
        resp = client.post("/dialogue", json={"text": "哈哈，好的"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_empty_422(self, client):
        resp = client.post("/dialogue", json={"text": "   "})
        assert resp.status_code == 422
