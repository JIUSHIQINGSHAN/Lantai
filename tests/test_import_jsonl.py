"""冷启动导入（Ticket 07）测试。

parse_import_lines 纯函数直调不 mock；落库用真实临时 SQLite（仅 mock
embedding/向量存储两个外部依赖）；REST 冒烟。
"""
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.models.tables import MemoryItem
from lantai.services.import_service import parse_import_lines
from lantai.storage.fts import init_fts


@pytest.fixture()
def imp_env():
    """内存 SQLite 真实建表 + FTS + patch 仅外部依赖。"""
    import lantai.models.tables  # noqa: F401
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
         patch("lantai.retrieval.hybrid.get_vector_store", return_value=vector_store_mock):
        yield session_factory, engine


# ── 纯函数：解析 ──────────────────────────────────────────────

def test_parse_ok():
    text = (
        '{"content": "第一条", "created_at": "2026-01-02T03:04:05+08:00", "lane": "fact"}\n'
        '{"content": "第二条", "tags": ["a", "b"]}\n'
    )
    valid, invalid = parse_import_lines(text)
    assert invalid == []
    assert [v["content"] for v in valid] == ["第一条", "第二条"]
    assert valid[0]["lane"] == "fact"
    assert valid[1]["lane"] == "general"  # 缺省取 RAW_MEMORY_DEFAULT_LANE
    # +08:00 → naive UTC（与摄取链 normalize_timestamp 同语义）
    assert valid[0]["created_at"] == datetime(2026, 1, 1, 19, 4, 5)
    assert valid[0]["created_at"].tzinfo is None
    assert valid[0]["updated_at"] is None
    assert valid[1]["tags"] == ["a", "b"]


def test_parse_invalid_reports_reasons():
    text = (
        '{"content": "好行"}\n'
        '{"content": ""}\n'
        '{"content": "时间坏", "created_at": "not-a-date"}\n'
        'not json at all\n'
        '{"lane": 5, "content": "lane坏"}\n'
        '{"content": "tags坏", "tags": [1, 2]}\n'
    )
    valid, invalid = parse_import_lines(text)
    assert len(valid) == 1
    reasons = [r["reason"] for r in invalid]
    assert any("content 缺失或为空" in r for r in reasons)
    assert any("created_at 时间戳无法解析" in r for r in reasons)
    assert any("JSON 解析失败" in r for r in reasons)
    assert any("lane 必须为非空字符串" in r for r in reasons)
    assert any("tags 必须为字符串数组" in r for r in reasons)
    # 物理行号正确
    assert {r["line"] for r in invalid} == {2, 3, 4, 5, 6}


def test_parse_blank_lines_skipped_not_invalid():
    valid, invalid = parse_import_lines('\n\n{"content": "x"}\n\n')
    assert len(valid) == 1 and invalid == []


# ── 落库（真实 SQLite，仅 mock 外部依赖）──────────────────────

def test_import_preserves_timestamps_and_dedups(imp_env):
    session_factory, engine = imp_env
    text = (
        '{"content": "历史记录甲", "created_at": "2026-01-02T03:04:05", '
        '"lane": "fact", "tags": ["旧"]}\n'
        '{"content": "历史记录乙", "created_at": "2025-12-31T23:59:00"}\n'
    )
    from lantai.services.import_service import run_jsonl_import
    report = run_jsonl_import(text)
    assert report["ok"] is True
    assert report["imported"] == 2 and report["duplicates"] == 0
    assert report["invalid"] == [] and report["errors"] == []

    with session_factory() as s:
        rows = s.exec(select(MemoryItem).order_by(MemoryItem.created_at)).all()
        assert len(rows) == 2
        # created_at 升序：乙（2025-12-31）在前，甲（2026-01-02）在后
        assert rows[0].content == "历史记录乙"
        assert rows[1].content == "历史记录甲"
        assert rows[1].memory_type == "verbatim"
        assert rows[1].created_at.year == 2026 and rows[1].created_at.month == 1
        assert rows[1].created_at.day == 2 and rows[1].created_at.hour == 3
        assert rows[1].updated_at == rows[1].created_at  # updated_at 缺省取 created_at
        assert rows[1].lane == "fact"
        assert rows[1].tags == ["旧"]

    # 重复导入幂等：全部 duplicate，不新增行
    report2 = run_jsonl_import(text)
    assert report2["imported"] == 0 and report2["duplicates"] == 2
    with session_factory() as s:
        assert len(s.exec(select(MemoryItem)).all()) == 2


def test_import_normalizes_tz_to_naive_utc(imp_env):
    """带时区（+08:00）输入落库为 naive UTC，digest 等 naive 区间比较一致。"""
    session_factory, _ = imp_env
    text = '{"content": "时区记录", "created_at": "2026-01-02T03:04:05+08:00"}\n'
    from lantai.services.import_service import run_jsonl_import
    report = run_jsonl_import(text)
    assert report["imported"] == 1
    with session_factory() as s:
        row = s.exec(select(MemoryItem)).one()
        assert row.created_at == datetime(2026, 1, 1, 19, 4, 5)
        assert row.created_at.tzinfo is None
        assert row.updated_at == row.created_at


def test_import_respects_agent_lane_bindings(imp_env, monkeypatch):
    """ACL 启用：越界 lane 行记 errors 不落库（宁 miss 不脏写）；未启用全量导入。"""
    from lantai.core.settings import settings
    session_factory, _ = imp_env
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {"agent-a": ["fact"]})
    text = (
        '{"content": "绑定lane事实", "lane": "fact"}\n'
        '{"content": "越界规则", "lane": "rule"}\n'
    )
    from lantai.services.import_service import run_jsonl_import
    report = run_jsonl_import(text, agent_id="agent-a")
    assert report["imported"] == 1
    assert len(report["errors"]) == 1
    assert "ACL" in report["errors"][0]["reason"]
    with session_factory() as s:
        rows = s.exec(select(MemoryItem)).all()
        assert len(rows) == 1
        assert rows[0].lane == "fact"
    # ACL 未启用（"no-acl" 哨兵）→ lane_allowed 恒真，全量导入
    monkeypatch.setattr(settings, "AGENT_LANE_BINDINGS", {})
    report2 = run_jsonl_import(text, agent_id="no-acl")
    assert report2["imported"] == 1 and report2["duplicates"] == 1


def test_import_invalid_lines_not_imported(imp_env):
    session_factory, _ = imp_env
    text = (
        '{"content": "合法行"}\n'
        '{"content": ""}\n'
        'bad json\n'
    )
    from lantai.services.import_service import run_jsonl_import
    report = run_jsonl_import(text)
    assert report["imported"] == 1
    assert len(report["invalid"]) == 2
    assert {r["line"] for r in report["invalid"]} == {2, 3}
    with session_factory() as s:
        assert len(s.exec(select(MemoryItem)).all()) == 1


# ── REST 冒烟 ────────────────────────────────────────────────

def test_import_endpoint(imp_env):
    from fastapi.testclient import TestClient

    from api_server import app
    with TestClient(app) as c:
        r = c.post("/import/jsonl", json={"text": '{"content": "端点导入"}\n'})
        assert r.status_code == 200
        body = r.json()
        assert body["imported"] == 1 and body["ok"] is True

        empty = c.post("/import/jsonl", json={"text": ""})
        assert empty.status_code == 422
