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
from lantai.models.tables import (MemoryCandidate, MemoryItem,
                                    MemoryProposal, ReflectRun, RetrievalEvent)


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



class TestReflectionDistribution:
    """反思提案可测量：拒绝数 + 类型×状态 + 置信桶（回填校准输入）。"""

    def test_reflection_distribution(self, digest_env):
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            MemoryProposal(id="p1", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "a"},
                           confidence=0.9, status="applied", created_at=now,
                           applied_at=now),
            MemoryProposal(id="p2", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "b"},
                           confidence=0.75, status="pending", created_at=now),
            MemoryProposal(id="p3", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "c"},
                           confidence=0.65, status="rejected", created_at=now),
            MemoryProposal(id="p4", proposal_type="deprecate", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "d"},
                           confidence=0.85, status="rejected", created_at=now),
        ])

        from lantai.workers.digest_worker import (collect_digest_stats,
                                                  render_digest_markdown)
        stats = collect_digest_stats()
        rf = stats["reflection"]
        assert rf["created"] == 4
        assert rf["applied"] == 1
        assert rf["pending"] == 1
        assert rf["rejected"] == 2
        assert rf["other"] == 0
        assert rf["by_type"] == {
            "add": {"applied": 1},
            "merge": {"pending": 1, "rejected": 1},
            "deprecate": {"rejected": 1},
        }
        assert rf["conf_buckets"] == {
            "0.5-0.6": 0, "0.6-0.7": 1, "0.7-0.8": 1,
            "0.8-0.9": 1, "0.9-1.0": 1,
        }

        md = render_digest_markdown(stats)
        assert "| 反思提案 | 今日 4（自动应用 1，待审 1，拒绝 2） |" in md
        assert "## 反思提案分布（今日新增）" in md
        assert "| add | 1 | 0 | 0 | 0 |" in md
        assert "| merge | 0 | 1 | 1 | 0 |" in md
        assert "| deprecate | 0 | 0 | 1 | 0 |" in md
        assert "| 合计 | 1 | 1 | 2 | 0 |" in md
        assert "置信桶（今日新增）" in md


    def test_reflection_window_consistency(self, digest_env):
        """窗口一致性：跨日应用的提案不计入今日；other 兜底使合计 == created。"""
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            # 昨日创建、今日应用：created/applied 均不计入今日窗口
            MemoryProposal(id="p1", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "a"},
                           confidence=0.9, status="applied",
                           created_at=now - timedelta(days=1), applied_at=now),
            # 今日创建、approved（非 applied/pending/rejected）→ other 兜底
            MemoryProposal(id="p2", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "b"},
                           confidence=0.8, status="approved", created_at=now),
        ])
        from lantai.workers.digest_worker import collect_digest_stats
        rf = collect_digest_stats()["reflection"]
        assert rf["created"] == 1
        assert rf["applied"] == 0
        assert rf["pending"] == 0
        assert rf["rejected"] == 0
        assert rf["other"] == 1
        assert (rf["applied"] + rf["pending"] + rf["rejected"] + rf["other"]
                == rf["created"])


class TestCalibrationStats:
    """回填校准输入：窗口聚合（类型×状态 + 置信桶 + 裁决原因 + 水位）。"""

    def test_collect_calibration_stats_window(self, digest_env):
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            MemoryProposal(id="p1", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "a"},
                           confidence=0.9, status="applied", created_at=now,
                           applied_at=now),
            MemoryProposal(id="p2", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "b"},
                           confidence=0.75, status="pending", created_at=now),
            MemoryProposal(id="p3", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "c"},
                           confidence=0.65, status="rejected", created_at=now,
                           decision_reason="证据不足，宁 miss"),
            MemoryProposal(id="p4", proposal_type="deprecate", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "d"},
                           confidence=0.85, status="rejected", created_at=now,
                           decision_reason="与新记忆冲突，需人工复核"),
            # 窗口外（旧提案）应排除
            MemoryProposal(id="p5", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "e"},
                           confidence=0.8, status="rejected",
                           created_at=now - timedelta(days=10)),
        ])
        _seed(session_factory, [
            MemoryItem(id="m1", memory_type="semantic", key="k1", content="a",
                       importance=1.2, created_at=now),
            MemoryItem(id="m2", memory_type="semantic", key="k2", content="b",
                       importance=2.8, created_at=now),
            MemoryItem(id="m3", memory_type="semantic", key="k3", content="c",
                       importance=9.0, created_at=now - timedelta(days=10)),
            # 未来时间戳记忆（created_at > now）也应排除（水位窗口有上界）
            MemoryItem(id="m4", memory_type="semantic", key="k4", content="d",
                       importance=99.0, created_at=now + timedelta(days=1)),
        ])
        _seed(session_factory, [
            ReflectRun(id="run1", run_at=now, waterline=4.0, skipped="idle"),
            ReflectRun(id="run2", run_at=now, waterline=6.5, skipped="",
                       proposals_created=1, auto_applied=1),
            ReflectRun(id="run3", run_at=now, waterline=3.0, skipped="",
                       proposals_created=0),
        ])

        from lantai.workers.digest_worker import collect_calibration_stats
        stats = collect_calibration_stats(days=7)
        assert stats["reflection"]["created"] == 4
        assert stats["reflection"]["applied"] == 1
        assert stats["reflection"]["pending"] == 1
        assert stats["reflection"]["rejected"] == 2
        assert stats["reflection"]["other"] == 0
        assert stats["reflection"]["by_type"] == {
            "add": {"applied": 1},
            "merge": {"pending": 1, "rejected": 1},
            "deprecate": {"rejected": 1},
        }
        assert stats["reflection"]["conf_buckets"] == {
            "0.5-0.6": 0, "0.6-0.7": 1, "0.7-0.8": 1,
            "0.8-0.9": 1, "0.9-1.0": 1,
        }
        assert stats["water_level"] == 4.0  # 仅窗口内两条（1.2+2.8）；窗外与未来均排除
        assert stats["reason_top"] == [
            ("证据不足，宁 miss", 1),
            ("与新记忆冲突，需人工复核", 1),
        ]
        assert stats["runs"]["total"] == 3
        assert stats["runs"]["idle"] == 1
        assert stats["runs"]["errored"] == 0
        assert stats["runs"]["llm_failed"] == 0
        assert stats["runs"]["productive"] == 1
        assert stats["runs"]["zero_outcome"] == 1
        assert stats["window_days"] == settings.REFLECT_IMPORTANCE_WINDOW_DAYS

    def test_render_calibration_markdown(self, digest_env):
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            MemoryProposal(id="p1", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "a"},
                           confidence=0.9, status="applied", created_at=now,
                           applied_at=now),
            MemoryProposal(id="p2", proposal_type="merge", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "b"},
                           confidence=0.65, status="rejected", created_at=now,
                           decision_reason="证据不足"),
        ])
        _seed(session_factory, [
            MemoryItem(id="m1", memory_type="semantic", key="k1", content="a",
                       importance=5.5, created_at=now),
            ReflectRun(id="run1", run_at=now, waterline=5.5, skipped="",
                       proposals_created=1),
        ])
        from lantai.workers.digest_worker import (
            collect_calibration_stats, render_calibration_markdown)
        stats = collect_calibration_stats(days=7)
        md = render_calibration_markdown(stats)
        assert "反思阈值回填校准（真实观察数据）" in md
        assert "| add | 1 | 0 | 0 | 0 |" in md
        assert "| merge | 0 | 0 | 1 | 0 |" in md
        assert "| 合计 | 1 | 0 | 1 | 0 |" in md
        assert "水位（窗口内 importance 累加）" in md and "5.5" in md
        assert "证据不足" in md
        assert "## 反思运行记录（窗口内）" in md
        assert "| 运行次数 | 1 |" in md

    def test_runs_five_way_classification(self, digest_env):
        """运行记录五分类互斥：空闲/异常/LLM 失败/产出/零产出。"""
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            ReflectRun(id="r_idle", run_at=now, skipped="idle"),
            ReflectRun(id="r_err", run_at=now, skipped="", error="boom"),
            ReflectRun(id="r_llm", run_at=now, skipped="",
                       curate_failed=True),
            ReflectRun(id="r_llm2", run_at=now, skipped="",
                       rejecter_failed=2, proposals_created=1),
            ReflectRun(id="r_prod", run_at=now, skipped="",
                       proposals_created=2, auto_applied=2),
            ReflectRun(id="r_zero", run_at=now, skipped="", proposals_created=0),
        ])
        from lantai.workers.digest_worker import collect_calibration_stats
        stats = collect_calibration_stats()  # 不带参数：默认窗口取 settings
        runs = stats["runs"]
        assert runs["total"] == 6
        assert runs["idle"] == 1
        assert runs["errored"] == 1
        assert runs["llm_failed"] == 2
        assert runs["productive"] == 1
        assert runs["zero_outcome"] == 1
        assert stats["window_days"] == settings.REFLECT_IMPORTANCE_WINDOW_DAYS

    def test_conf_bucket_outlier_lands_in_other(self, digest_env):
        """桶外置信（<0.5）计入「其他」兜底桶，不静默丢失。"""
        session_factory = digest_env
        now = _utc_naive(datetime.now(timezone.utc))
        _seed(session_factory, [
            MemoryProposal(id="p1", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "a"},
                           confidence=0.9, status="applied", created_at=now),
            MemoryProposal(id="p2", proposal_type="add", candidate_id=None,
                           evidence_ids=["e1"], proposed_patch={"key": "b"},
                           confidence=0.3, status="rejected", created_at=now),
        ])
        from lantai.workers.digest_worker import (collect_digest_stats,
                                                  render_digest_markdown)
        rf = collect_digest_stats()["reflection"]
        assert rf["conf_buckets"]["其他"] == 1
        assert rf["conf_buckets"]["0.5-0.6"] == 0
        md = render_digest_markdown(collect_digest_stats())
        assert "[其他]×1" in md


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
        # 反思/蒸馏统计行（零迁移标识 candidate_id IS NULL）；今日检索行保持单行完整
        assert "| 反思提案 | 今日 0（自动应用 0，待审 0，拒绝 0） |" in content
        retr_line = next(l for l in content.splitlines() if l.startswith("| 今日检索 |"))
        assert "系统噪音" in retr_line and retr_line.endswith("） |")
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