"""MCP `add` 的 media_url 接线测试（票 `.scratch/v0.68-next-window-2026-11/07-mcp-add-media-url-unwired.md`）。

病灶：`add` 工具 inputSchema 广告了 `media_url`（目识），`handle_add` 构造
`AddMemoryReq(title/content/lane)` 不读它——传了即静默丢弃（或 content 校验
挡下），广告即假承诺。REST `POST /add` 同一 `AddMemoryReq` 目识链真实生效。

三臂 + 一哨兵，真内存库直调 `handle_add`（不 mock 被测链路；vision_caption /
extract_candidate 外部替身，口径镜像 `tests/test_vision.py` 的 REST 侧先例——
两入口必须同语义）：
1. 主臂：media_url + 空 content → 走真目识链落库（caption 作正文、provenance
   带 vision-caption 与 media_url）；
2. 对照臂：不传 media_url 行为逐字不变（普通提取路径，provenance 非 vision）；
3. 降级臂：vision 外部失败 → RuntimeError 照传（宁 miss 不脏写，不落库）——
   与 REST 侧 `test_add_vision_failure_no_write` 同语义；
4. 哨兵：`add` inputSchema 广告的可选字段必须被 `handle_add` 真实消费（防再
   假承诺——广告面 ⊆ 消费面）。
"""

import inspect
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.cli.mcp as mcp
import lantai.storage.db as db_module
from lantai.models.tables import MemoryCandidate, RawDocument

_CAPTION = "画面主体是一台服务器机柜，标注端口 8080，机房维护现场。"


def _mock_extract():
    return {
        "topic": ["图片"],
        "summary": "服务器机柜维护现场",
        "claims": ["画面主体是服务器机柜"],
        "methods": [],
        "constraints": [],
        "actions": [],
        "extractor_confidence": 0.9,
    }


@pytest.fixture()
def mcp_env():
    import lantai.models.tables  # noqa: F401
    from lantai.storage.fts import init_fts

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
        patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock),
        patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock),
    ):
        yield session_factory, engine


class TestHandleAddMediaUrlWired:
    def test_media_url_goes_through_real_vision_chain(self, mcp_env):
        """主臂：media_url 进 handler 后走与 REST 同一条真目识链（caption 作正文落库）。"""
        session_factory, _ = mcp_env
        with (
            patch("lantai.services.vision_service.vision_caption", return_value=_CAPTION),
            patch("lantai.parsing.extractor.extract_candidate", return_value=_mock_extract()),
        ):
            out = mcp.handle_add(
                {"title": "机房照片", "content": "", "media_url": "https://example.com/rack.png"}
            )
        assert "document_id" in out and "candidate_id" in out, f"目识链未走通：{out}"
        with session_factory() as s:
            doc = s.get(RawDocument, out["document_id"])
            assert doc is not None and doc.content.startswith(_CAPTION[:10])
            cand = s.get(MemoryCandidate, out["candidate_id"])
            assert cand.provenance["prompt"] == "vision-caption"
            assert cand.provenance["media_url"] == "https://example.com/rack.png"

    def test_without_media_url_behavior_unchanged(self, mcp_env):
        """对照臂：不传 media_url 的普通写入逐字不变（非 vision 路径）。"""
        session_factory, _ = mcp_env
        with (
            patch("lantai.services.vision_service.vision_caption", return_value=_CAPTION),
            patch("lantai.parsing.extractor.extract_candidate", return_value=_mock_extract()),
        ):
            out = mcp.handle_add(
                {
                    "title": "普通记忆",
                    "content": "这是一段超过十个字的普通正文内容",
                    "lane": "general",
                }
            )
        assert "document_id" in out and "candidate_id" in out
        with session_factory() as s:
            doc = s.get(RawDocument, out["document_id"])
            assert doc.content == "这是一段超过十个字的普通正文内容"
            cand = s.get(MemoryCandidate, out["candidate_id"])
            assert cand.provenance.get("prompt") != "vision-caption"

    def test_vision_failure_propagates_and_writes_nothing(self, mcp_env):
        """降级臂：vision 外部失败 → RuntimeError 照传、零落库（与 REST 同语义）。"""
        session_factory, _ = mcp_env
        with (
            patch(
                "lantai.services.vision_service.vision_caption",
                side_effect=RuntimeError("vision api down"),
            ),
            pytest.raises(RuntimeError),
        ):
            mcp.handle_add(
                {"title": "坏图", "content": "", "media_url": "https://example.com/bad.png"}
            )
        with session_factory() as s:
            assert s.query(RawDocument).count() == 0
            assert s.query(MemoryCandidate).count() == 0


class TestAdvertisedFieldsAreConsumed:
    def test_add_schema_fields_all_read_by_handler(self):
        """哨兵：add 广告面 ⊆ handle_add 消费面（防再假承诺，票 07 案 2）。"""
        advertised = set(mcp.TOOLS["add"]["inputSchema"]["properties"].keys())
        handler_src = inspect.getsource(mcp.handle_add)
        missing = [k for k in advertised if k not in handler_src]
        assert not missing, f"add inputSchema 广告了但 handler 未消费的字段：{missing}"
