"""Ticket 03: Daily Digest 每日盘点报告

核心函数冒烟测试（不 mock 内部逻辑；仅隔离 DB 与报告输出目录）：
- collect_digest_stats：五项统计数字正确（新增/修改记忆、待审、归档、检索）
- run_digest_once：生成当日报告文件，内容含统计数字
- load_today_digest：今日报告读取（未生成则生成）
- REST GET /digest/today：返回报告内容
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import MemoryCandidate, MemoryItem, RetrievalEvent


def _utc_naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


@pytest.fixture()
def digest_env(tmp_path, monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session + 报告输出目录隔离。"""
    import lantai.models.tables  # noqa: F401  注册全部表
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", str(tmp_path / "digest"))
    return session_factory


def _seed(session_factory, rows):
    with session_factory() as s:
        for row in rows:
            s.add(row)
        s.commit()


class TestCollectStats:
    """五项统计数字正确（真实 DB 查询）。"""

    def test_window_counts(self, digest_env):
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        old = now - timedelta(days=2)
        _seed(session_factory, [
            # 今日新增记忆 1（mem_a）；mem_c 今日更新（修改数 1）；mem_b 在窗外
            MemoryItem(id="mem_a", memory_type="semantic", key="a", content="A",
                       created_at=now, updated_at=now),
            MemoryItem(id="mem_b", memory_type="semantic", key="b", content="B",
                       created_at=now - timedelta(days=5),
                       updated_at=now - timedelta(days=5)),
            MemoryItem(id="mem_c", memory_type="semantic", key="c", content="C",
                       created_at=old, updated_at=now),
            # 待审：今日新增 1 + 老待审 1
            MemoryCandidate(id="cand_p1", document_id="doc1", status="pending_review",
                            created_at=now),
            MemoryCandidate(id="cand_p2", document_id="doc2", status="pending_review",
                            created_at=old),
            # 归档：今日创建即归档 1，窗外 1
            MemoryCandidate(id="cand_r1", document_id="doc3", status="rejected",
                            created_at=now),
            MemoryCandidate(id="cand_r2", document_id="doc4", status="rejected",
                            created_at=old),
            # 检索：今日 3（1 零结果、1 噪音），窗外 1
            RetrievalEvent(id="rev_1", trace_id="t1", query_norm_hash="h1",
                           param_snapshot_hash="p1", latency_ms=100,
                           zero_result=False, is_system_noise=False, created_at=now),
            RetrievalEvent(id="rev_2", trace_id="t2", query_norm_hash="h2",
                           param_snapshot_hash="p2", latency_ms=300,
                           zero_result=True, is_system_noise=False, created_at=now),
            RetrievalEvent(id="rev_3", trace_id="t3", query_norm_hash="h3",
                           param_snapshot_hash="p3", latency_ms=50,
                           zero_result=False, is_system_noise=True, created_at=now),
            RetrievalEvent(id="rev_4", trace_id="t4", query_norm_hash="h4",
                           param_snapshot_hash="p4", latency_ms=10,
                           zero_result=False, is_system_noise=False, created_at=old),
        ])

        from lantai.workers.digest_worker import collect_digest_stats
        stats = collect_digest_stats()
        assert stats["memories"] == {"new": 1, "modified": 1, "total": 3}
        assert stats["pending"] == {"total": 2, "new_today": 1}
        assert stats["archived"]["created_today"] == 1
        assert stats["retrieval"]["total"] == 3
        assert stats["retrieval"]["zero_result"] == 1
        assert stats["retrieval"]["noise"] == 1
        assert stats["retrieval"]["avg_latency_ms"] == 150.0


class TestRunDigest:
    """报告文件生成 + TTL 归档数回填。"""

    def test_run_digest_once_writes_report(self, digest_env):
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            MemoryItem(id="mem_a", memory_type="semantic", key="a", content="A",
                       created_at=now, updated_at=now),
            MemoryCandidate(id="cand_p1", document_id="doc1", status="pending_review",
                            created_at=now),
        ])

        from lantai.workers.digest_worker import run_digest_once
        result = run_digest_once()
        assert result["ok"] is True
        path = Path(result["path"])
        assert path.exists()
        assert path.name.endswith(".md")
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# 记忆日报")
        assert "| 新增记忆 | 1 |" in content
        assert "| 待审候选 | 1（今日新增 1） |" in content
        assert "待审候选提醒" in content
        assert result["stats"]["archived"]["ttl"] == 0


class TestLoadTodayDigest:
    """REST/MCP 读取入口：未生成则生成，已生成则读文件。"""

    def test_generates_then_reads(self, digest_env):
        session_factory = digest_env
        _seed(session_factory, [
            MemoryItem(id="mem_a", memory_type="semantic", key="a", content="A"),
        ])

        from lantai.workers.digest_worker import load_today_digest
        res = load_today_digest()
        assert res["ok"] is True
        assert res["content"].startswith("# 记忆日报")
        assert Path(res["path"]).exists()
        res2 = load_today_digest()
        assert res2["content"] == res["content"]


class TestDigestRoute:
    """REST 入口 GET /digest/today。"""

    def test_get_today(self, digest_env):
        from fastapi.testclient import TestClient
        from api_server import app
        with TestClient(app) as c:
            resp = c.get("/digest/today")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["content"].startswith("# 记忆日报")
        assert "stats" in data