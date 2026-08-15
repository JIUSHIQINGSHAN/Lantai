"""Obsidian 双链 + 原文直存（Ticket 02）核心函数冒烟测试（不 mock 内部逻辑）。

测试纪律：mock 仅用于外部依赖（embedding 网络、向量存储、意图 LLM）；
extract_wikilinks / sync_obsidian_note 的产品代码（SQLite 写入 / FTS 同步 /
实体边沉淀 / 幂等去重）真实执行。
"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

import lantai.storage.db as db_module
from lantai.models.schemas import ObsidianSyncReq
from lantai.models.tables import MemoryEdge, MemoryItem


@pytest.fixture()
def obs_env():
    """内存 SQLite 真实建表 + FTS 初始化 + patch 仅外部依赖。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts
    init_fts(engine.raw_connection())

    def session_factory() -> Session:
        return Session(engine)

    vector_store_mock = Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock())
    with patch.object(db_module, "get_session", session_factory), \
         patch("lantai.llm.client.embed", return_value=[[0.1] * 8]), \
         patch("lantai.services.memory_service.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]), \
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock), \
         patch("lantai.retrieval.intent.chat_json",
               return_value={"intent": "fact_lookup", "reason": "test"}):
        yield session_factory, engine, vector_store_mock


def test_extract_wikilinks_basic():
    """纯函数冒烟：[[页面]] / [[页面|别名]] 解析，锚点忽略，保序去重。"""
    from lantai.services.obsidian_service import extract_wikilinks
    text = "参考 [[部署手册]] 和 [[部署手册|别名版]]，以及 [[#锚点]] 与 [[ ]] 空链。"
    assert extract_wikilinks(text) == ["部署手册"]
    assert extract_wikilinks("无双链纯文本") == []


def test_sync_obsidian_note_persists_note_entities_edges(obs_env):
    """真实 DB：笔记 verbatim 落库 + 标题/双链实体沉淀 + links 边。"""
    session_factory, engine, _ = obs_env
    from lantai.services.obsidian_service import sync_obsidian_note

    result = sync_obsidian_note(ObsidianSyncReq(
        title="上线复盘", content="按 [[部署手册]] 执行，注意 [[备份]] 策略。"))
    assert result["dedup"] is False
    assert set(result["entities"]) == {"上线复盘", "部署手册", "备份"}

    with session_factory() as s:
        note = s.get(MemoryItem, result["note_id"])
        assert note is not None and note.memory_type == "verbatim"
        ents = s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "entity")).all()
        assert {e.key for e in ents} == {"上线复盘", "部署手册", "备份"}
        links = s.exec(select(MemoryEdge).where(
            MemoryEdge.relation == "links")).all()
        assert len(links) == 3
        assert all(e.source_memory_id == result["note_id"] for e in links)


def test_sync_obsidian_note_idempotent(obs_env):
    """重复推送：note 复用 + 实体不重复 + links 不重复。"""
    session_factory, engine, _ = obs_env
    from lantai.services.obsidian_service import sync_obsidian_note

    req = ObsidianSyncReq(title="运维", content="见 [[故障手册]]")
    r1 = sync_obsidian_note(req)
    r2 = sync_obsidian_note(req)
    assert r1["note_id"] == r2["note_id"]
    assert r2["dedup"] is True
    with session_factory() as s:
        ents = s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "entity")).all()
        assert len(ents) == 2  # 运维 + 故障手册，不重复
        links = s.exec(select(MemoryEdge).where(
            MemoryEdge.relation == "links")).all()
        assert len(links) == 2


def test_verbatim_excluded_from_default_recall(obs_env):
    """默认混合召回排除 verbatim（VERBATIM_IN_RECALL=false）；专用通道可查。"""
    session_factory, engine, _ = obs_env
    from lantai.models.schemas import RawMemoryReq
    from lantai.retrieval.hybrid import hybrid_search
    from lantai.services.memory_service import add_raw_memory

    content = "系统部署手册：备份命令 mysqldump -u root db > backup.sql"
    add_raw_memory(RawMemoryReq(content=content))

    default = hybrid_search("mysqldump 备份命令", top_k=5, use_rerank=False)
    assert all("mysqldump" not in (r.get("memory", {}).get("content", ""))
               for r in default)

    scoped = hybrid_search("mysqldump 备份命令", top_k=5, use_rerank=False,
                           memory_types=["verbatim"])
    texts = [r.get("memory", {}).get("content", "") for r in scoped]
    assert any(content in t for t in texts)


def test_obsidian_route_wiring(obs_env):
    """REST 接线：POST /obsidian/sync 与 GET /verbatim/search 可达。"""
    session_factory, engine, _ = obs_env
    from fastapi.testclient import TestClient
    from api_server import app

    with TestClient(app) as c:
        resp = c.post("/obsidian/sync", json={
            "title": "复盘", "content": "参考 [[部署手册]]"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["entities"] == ["复盘", "部署手册"]
        s2 = c.get("/verbatim/search", params={"q": "部署手册"})
        assert s2.status_code == 200
