"""瞭望（后台监控面板）冒烟测试。

测试纪律（AGENTS.md）：核心函数必须有不 mock 的冒烟测试——真实构造最小输入
直调函数，验证主路径不炸。build_monitor_snapshot 是纯函数，直传内存 session；
HTTP 层用 TestClient 真起 app（鉴权依赖真实走 DB dev fallback）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api_server import app
from lantai.core.ids import new_id
from lantai.models.tables import (
    ApiKey,
    ConflictEvent,
    IngestJob,
    MemoryCandidate,
    MemoryItem,
    MemoryProposal,
    RetrievalEvent,
    SkillCrystal,
)
from lantai.ops.monitor import (
    build_monitor_snapshot,
    memory_daily_series,
    probe_health,
    worker_specs,
)

# ---------------------------------------------------------------- 纯函数冒烟

def _seed(session: Session) -> None:
    """构造最小但有区分度的监控数据。"""
    now = datetime.now(UTC)
    session.add(MemoryItem(
        id=new_id("mem"), key="user/coffee", content="用户喝无糖咖啡",
        lane="preference", domain="user", status="active",
        tier="working", decay_class="episodic", created_at=now - timedelta(hours=2),
    ))
    session.add(MemoryItem(
        id=new_id("mem"), key="rule/deploy", content="部署必须单进程",
        lane="rule", domain="agent", status="active",
        tier="core", decay_class="procedural", created_at=now - timedelta(days=3),
    ))
    session.add(MemoryItem(
        id=new_id("mem"), key="old/chat", content="旧闲聊",
        lane="chat", domain="session", status="archived",
        tier="working", decay_class="episodic", created_at=now - timedelta(days=30),
    ))
    session.add(MemoryCandidate(
        id=new_id("cand"), document_id="doc_seed", summary="待审候选：喜欢深色主题",
        status="pending_review",
        extractor_confidence=0.8, created_at=now - timedelta(hours=1),
    ))
    session.add(MemoryProposal(
        id=new_id("prop"), proposal_type="change", reason="测试提案",
        status="pending", confidence=0.9, created_at=now - timedelta(days=2),
    ))
    session.add(ConflictEvent(
        id=new_id("conf"), memory_id="mem_x", rule_name="mutex_fact",
        incoming_ref="冲突测试", status="open", created_at=now - timedelta(hours=5),
    ))
    session.add(SkillCrystal(
        id=new_id("cry"), skill_name="部署检查", trigger_rule="上线前",
        procedure="跑测试", status="candidate",
        candidate_count=3, hit_count=5, created_at=now - timedelta(days=1),
    ))
    session.add(IngestJob(
        id=new_id("job"), source_id="src_test", status="failed",
        error="模拟摄取失败", started_at=now - timedelta(hours=3),
        finished_at=now - timedelta(hours=3) + timedelta(minutes=2),
    ))
    # 检索事件：10 条真实 + 2 条零召回 + 1 条系统噪音
    for i in range(10):
        session.add(RetrievalEvent(
            id=new_id("rev"), trace_id="t", query_text=f"真实查询{i}",
            query_norm_hash=f"h{i}", lane="user", intent_bucket="recall",
            param_snapshot_hash="x", result_ids=[f"mem_{i}"], result_scores=[0.9],
            latency_ms=100 + i * 50, zero_result=False,
            estimated_tokens=200, created_at=now - timedelta(hours=i + 1),
        ))
    for i in range(2):
        session.add(RetrievalEvent(
            id=new_id("rev"), trace_id="t", query_text=f"零召回查询{i}",
            query_norm_hash=f"z{i}", lane="user", intent_bucket="recall",
            param_snapshot_hash="x", result_ids=[], result_scores=[],
            latency_ms=300, zero_result=True,
            estimated_tokens=50, created_at=now - timedelta(hours=i + 1),
        ))
    session.add(RetrievalEvent(
        id=new_id("rev"), trace_id="t",
        query_text="review the conversation above and consider saving to memory",
        query_norm_hash="noise", lane="", intent_bucket=None,
        param_snapshot_hash="x", result_ids=[], result_scores=[],
        latency_ms=10, zero_result=True, is_system_noise=True,
        estimated_tokens=10, created_at=now - timedelta(minutes=30),
    ))
    session.commit()


def test_build_monitor_snapshot_smoke(param_env):
    """核心聚合函数：真实数据直调，主路径不炸且口径正确。"""
    session_factory, _engine = param_env
    with session_factory() as s:
        _seed(s)
    with session_factory() as s:
        snap = build_monitor_snapshot(s)

    # 顶层结构完整
    for key in ("generated_at", "health", "alerts", "runtime", "storage",
                "memories", "queues", "workers", "retrieval", "ingestion",
                "coalesce_buffer"):
        assert key in snap, f"缺少快照字段 {key}"

    # 记忆存量与分布
    assert snap["memories"]["total"] == 3
    assert snap["memories"]["active"] == 2
    assert snap["memories"]["archived"] == 1
    assert snap["memories"]["by_lane"]["preference"] == 1
    assert snap["memories"]["by_decay_class"]["procedural"] == 1

    # 闸门队列
    q = snap["queues"]
    assert q["candidates_pending"] == 1
    assert q["proposals_pending"] == 1
    assert q["conflicts_open"] == 1
    assert q["crystals_candidate"] == 1

    # 检索质量：12 真实（10 命中 + 2 零召回），噪音 1 条被排除
    r24 = snap["retrieval"]["last_24h"]
    assert r24["total"] == 13
    assert r24["real"] == 12
    assert r24["system_noise"] == 1
    assert r24["zero_recall"] == 2
    assert r24["zero_recall_rate"] == round(2 / 12, 4)
    # p95 延迟：12 个延迟排序后第 95 分位
    assert r24["latency_p95_ms"] >= r24["latency_avg_ms"]
    assert r24["estimated_tokens"] == 10 * 200 + 2 * 50

    # 慢查询榜按延迟降序
    slow = snap["retrieval"]["slow_queries"]
    assert len(slow) <= 10
    assert slow[0]["latency_ms"] >= slow[-1]["latency_ms"]

    # 吞吐双序列 7 天对齐
    series = snap["retrieval"]["daily_series"]
    assert len(series) == 7
    assert all({"date", "new_memories", "retrievals"} <= set(d) for d in series)

    # 摄取失败被捕获
    assert snap["ingestion"]["jobs_failed_24h"] == 1
    assert snap["ingestion"]["recent_jobs"][0]["status"] == "failed"

    # 告警：冲突 + 摄取失败必现
    codes = {a["code"] for a in snap["alerts"]}
    assert "ingest:failed" in codes
    assert "queue:conflicts" in codes

    # worker 规格与状态
    names = {w["name"] for w in snap["workers"]}
    assert {"ingest", "evolve", "forgetting", "digest", "reflect", "autodream"} <= names
    for w in snap["workers"]:
        assert w["state"] in {"ok", "overdue", "disabled", "never_run"}


def test_probe_health_returns_all_dependencies(param_env):
    """健康探测：三项依赖都给出状态（异常不外抛）。"""
    session_factory, _ = param_env
    with session_factory() as s:
        checks = probe_health(s)
    assert set(checks) == {"sqlite", "chromadb", "llm"}
    for c in checks.values():
        assert c["status"] in {"ok", "degraded", "unknown"}
        assert isinstance(c["detail"], str) and c["detail"]


def test_worker_specs_intervals_positive():
    """worker 周期规格：启用项周期为正整数秒。"""
    for name, spec in worker_specs().items():
        assert isinstance(spec["seconds"], int) and spec["seconds"] > 0
        assert spec["label"]


def test_memory_daily_series_pads_missing_days(param_env):
    """每日新增序列：缺日补零、长度为 7。"""
    session_factory, _ = param_env
    with session_factory() as s:
        _seed(s)
    with session_factory() as s:
        series = memory_daily_series(s, datetime.now(UTC), days=7)
    assert len(series) == 7
    # 3 条记忆中 2 条在近 7 天（30 天前的旧闲聊在窗外）
    assert sum(d["new_memories"] for d in series) == 2
    assert series[-1]["new_memories"] >= 1  # 今天有新增


def test_worker_overdue_detection(param_env, monkeypatch):
    """漏跑判定：上次运行远超周期 → overdue；从未跑但刚启动 → 不报。"""
    from lantai.ops import monitor as mon
    session_factory, _ = param_env

    # ingest 周期 60 分钟；上次运行在 5 小时前 → overdue
    monkeypatch.setattr(mon.scheduler, "get_last_run",
                        lambda name: (datetime.now(UTC) - timedelta(hours=5)).isoformat()
                        if name == "ingest" else None)
    with session_factory() as s:
        snap = build_monitor_snapshot(s)
    workers = {w["name"]: w for w in snap["workers"]}
    assert workers["ingest"]["state"] == "overdue"
    assert workers["ingest"]["age_seconds"] is not None
    # digest 从未运行但进程刚启动（started_at=now）→ ok（宽限 10 分钟）
    assert workers["digest"]["state"] in {"ok", "disabled"}


# ---------------------------------------------------------------- HTTP 层冒烟

def _purge_tester_keys() -> None:
    """清除测试密钥（dev 模式回退要求 DB 无任何 ApiKey 行）。"""
    from lantai.storage import db as db_module
    with db_module.get_session() as s:
        for obj in s.exec(select(ApiKey).where(ApiKey.user_id == "tester")).all():
            s.delete(obj)
        s.commit()


def test_monitor_snapshot_endpoint_served():
    """GET /monitor/snapshot：dev 模式（DB 无密钥）放行，返回完整快照。"""
    _purge_tester_keys()
    try:
        with TestClient(app) as client:
            resp = client.get("/monitor/snapshot")
            assert resp.status_code == 200
            data = resp.json()
            assert data["health"]["overall"] in {"ok", "degraded", "unknown"}
            assert "workers" in data and len(data["workers"]) >= 6
            assert "retrieval" in data and "daily_series" in data["retrieval"]

            resp2 = client.get("/monitor/health")
            assert resp2.status_code == 200
            body = resp2.json()
            assert "ok" in body and "alerts" in body and "overall" in body
    finally:
        _purge_tester_keys()


def test_monitor_endpoint_rejects_bad_bearer():
    """DB 中存在密钥时（非 dev 模式），无/错 Bearer 请求被拒。"""
    from lantai.core.auth import create_api_key
    from lantai.storage import db as db_module

    _purge_tester_keys()
    raw_key, api_key = create_api_key("tester", ["general"])
    key_hash = api_key.key_hash
    with db_module.get_session() as s:
        s.add(api_key)
        s.commit()
    try:
        with TestClient(app) as client:
            assert client.get("/monitor/snapshot").status_code == 401
            assert client.get("/monitor/snapshot",
                              headers={"Authorization": "Bearer wrong-key"}).status_code == 401
            ok = client.get("/monitor/snapshot",
                            headers={"Authorization": f"Bearer {raw_key}"})
            assert ok.status_code == 200
    finally:
        with db_module.get_session() as s:
            for obj in s.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).all():
                s.delete(obj)
            s.commit()


def test_ui_serves_monitor_dashboard():
    """控制台首页包含瞭望台监控面板标记。"""
    with TestClient(app) as client:
        page = client.get("/ui")
        assert page.status_code == 200
        assert "瞭望台" in page.text
        assert "monRefreshBtn" in page.text
