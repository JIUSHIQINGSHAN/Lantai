"""目识（vision）多模态记忆（v0.10）测试。

validate_media_url / schema 二选一 / build_vision_memory 纯函数直调不 mock；
add 全链路用真实 SQLite+FTS，仅 mock 外部 LLM（vision_caption / extract_candidate
/ embedding）与向量存储。
"""
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.models.schemas import AddMemoryReq
from lantai.models.tables import MemoryCandidate, RawDocument


@pytest.fixture()
def vision_env():
    import lantai.models.tables  # noqa: F401
    from lantai.storage.fts import init_fts
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.storage.vector_store.get_vector_store", return_value=vector_store_mock):
        yield session_factory, engine


# ── 纯函数：media_url 校验（不 mock）────────

def test_validate_media_url_scheme_whitelist():
    from lantai.ingestion.safety import validate_media_url
    assert validate_media_url("https://example.com/a.png") == "https://example.com/a.png"
    assert validate_media_url("http://example.com/a.png") == "http://example.com/a.png"
    assert validate_media_url("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    with pytest.raises(ValueError, match="scheme"):
        validate_media_url("ftp://example.com/a.png")
    with pytest.raises(ValueError, match="non-empty"):
        validate_media_url("")
    with pytest.raises(ValueError, match="host"):
        validate_media_url("https:///a.png")


# ── schema 二选一校验（不 mock）────────

def test_add_req_content_or_media_exclusive():
    AddMemoryReq(title="t", content="图片描述内容超过十字")
    AddMemoryReq(title="t", content="", media_url="https://example.com/a.png")
    with pytest.raises(ValidationError, match="二选一"):
        AddMemoryReq(title="t", content="图片描述内容超过十字",
                     media_url="https://example.com/a.png")
    with pytest.raises(ValidationError, match="content required"):
        AddMemoryReq(title="t", content="", media_url="")


# ── build_vision_memory（mock 外部 vision 网络）────────

def test_build_vision_memory_injects_caption():
    from lantai.services.vision_service import build_vision_memory
    req = AddMemoryReq(title="图", content="", media_url="https://example.com/a.png")
    with patch("lantai.services.vision_service.vision_caption",
               return_value="画面主体是一台服务器机柜，标注了「端口 8080」，氛围是机房维护现场。"):
        out = build_vision_memory(req)
    assert out.content.startswith("画面主体是一台服务器机柜")
    assert out.media_url == "https://example.com/a.png"
    assert req.content == ""  # 原 req 不变（model_copy 语义）


def test_build_vision_memory_empty_caption_raises():
    """空 caption 拒绝落库（宁 miss 不脏写，不落失败文本）。"""
    from lantai.services.vision_service import build_vision_memory
    req = AddMemoryReq(title="图", content="", media_url="https://example.com/a.png")
    with patch("lantai.services.vision_service.vision_caption", return_value="   "):
        with pytest.raises(ValueError, match="拒绝落库"):
            build_vision_memory(req)


def test_build_vision_memory_no_media_passthrough():
    from lantai.services.vision_service import build_vision_memory
    req = AddMemoryReq(title="普通", content="普通文字记忆内容足够长")
    out = build_vision_memory(req)
    assert out is req  # 零开销原样返回


# ── add 全链路（真实 SQLite+FTS，仅 mock 外部 LLM）────────

def _mock_extract():
    return {
        "topic": ["图片"], "summary": "服务器机柜维护现场",
        "claims": ["画面主体是服务器机柜"], "methods": [], "constraints": [],
        "actions": [], "extractor_confidence": 0.9,
    }


def test_add_vision_end_to_end(vision_env):
    session_factory, _ = vision_env
    from lantai.services.memory_service import add_memory
    req = AddMemoryReq(title="机房照片", content="", media_url="https://example.com/rack.png")
    with patch("lantai.services.vision_service.vision_caption",
               return_value="画面主体是一台服务器机柜，标注端口 8080，机房维护现场。"), \
         patch("lantai.parsing.extractor.extract_candidate",
               return_value=_mock_extract()):
        out = add_memory(req)
    assert "document_id" in out and "candidate_id" in out
    with session_factory() as s:
        doc = s.get(RawDocument, out["document_id"])
        assert doc.content.startswith("画面主体是一台服务器机柜")
        cand = s.get(MemoryCandidate, out["candidate_id"])
        assert cand.provenance["prompt"] == "vision-caption"
        assert cand.provenance["media_url"] == "https://example.com/rack.png"
        assert cand.provenance["model"]


def test_add_vision_failure_no_write(vision_env):
    """vision 失败（网络异常）→ 不落任何库（宁 miss 不脏写）。"""
    session_factory, _ = vision_env
    from lantai.services.memory_service import add_memory
    req = AddMemoryReq(title="坏图", content="", media_url="https://example.com/bad.png")
    with patch("lantai.services.vision_service.vision_caption",
               side_effect=RuntimeError("vision api down")), pytest.raises(RuntimeError):
        add_memory(req)
    with session_factory() as s:
        assert s.exec(select_count(RawDocument)).one() == 0
        assert s.exec(select_count(MemoryCandidate)).one() == 0


def select_count(model):
    from sqlmodel import func, select
    return select(func.count()).select_from(model)


# ── v0.12 截屏入忆：data URI 严格校验（不 mock）────────

def test_validate_media_url_data_uri_rules():
    import base64

    from lantai.ingestion.safety import validate_media_url
    tiny = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30).decode()
    uri = f"data:image/png;base64,{tiny}"
    assert validate_media_url(uri) == uri
    with pytest.raises(ValueError, match="must be data:image"):
        validate_media_url("data:text/plain;base64,AAAA")
    with pytest.raises(ValueError, match="type not allowed"):
        validate_media_url("data:image/svg+xml;base64,AAAA")
    with pytest.raises(ValueError, match="not valid base64"):
        validate_media_url("data:image/png;base64,!!!not-base64!!!")
    with pytest.raises(ValueError, match="empty payload"):
        validate_media_url("data:image/png;base64,")


def test_validate_media_url_data_uri_too_large(monkeypatch):
    import base64

    from lantai.core.settings import settings
    from lantai.ingestion.safety import validate_media_url
    monkeypatch.setattr(settings, "MEDIA_DATA_URI_MAX_BYTES", 10)
    big = base64.b64encode(b"x" * 11).decode()
    with pytest.raises(ValueError, match="too large"):
        validate_media_url(f"data:image/png;base64,{big}")


def test_add_req_media_url_long_data_uri_allowed():
    """v0.12 截屏：data URI 可达 MB 级，schema 长度上限已放宽（旧 2000 拒绝）。"""
    long_uri = "data:image/png;base64," + "A" * 3000
    req = AddMemoryReq(title="截图", content="", media_url=long_uri)
    assert req.media_url == long_uri
