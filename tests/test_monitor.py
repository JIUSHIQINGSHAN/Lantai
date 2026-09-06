"""司天（ADR-0044）后台运行监控面板：采集器 / 聚合 / 告警 / 路由 / 遥测落库。

测试纪律（AGENTS.md）：核心函数一律不 mock 内部逻辑——
- `MetricsCollector` / `worker_staleness` / `evaluate_alerts` / `render_prometheus`
  用真实构造的最小输入直调；
- `build_monitor_snapshot` 直传真实建表的内存 SQLite session，聚合走真 SQL；
- 中间件与路由用 TestClient 打真实 HTTP 链路（只 mock 外部网络，不 mock 被测逻辑）。
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.scheduler import worker_staleness
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem, SchedulerRun
from lantai.observability import metrics as metrics_module
from lantai.observability.metrics import MetricsCollector, normalize_route, percentile
from lantai.observability.telemetry import (
    TelemetryWriter,
    flush_telemetry,
    set_writer,
    should_persist,
)
from lantai.ops.monitor import (
    build_monitor_snapshot,
    evaluate_alerts,
    list_operation_logs,
    render_prometheus,
    safe_settings_view,
)


@pytest.fixture()
def monitor_env():
    """内存 SQLite 真实建表 + 全新指标收集器 + 独立遥测落库器。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    collector = MetricsCollector(buffer=200, bucket_minutes=30)
    metrics_module.set_collector(collector)
    set_writer(TelemetryWriter(flush_seconds=3600))
    with patch.object(db_module, "get_session", session_factory):
        yield session_factory, engine, collector
    metrics_module.set_collector(None)
    set_writer(None)


def _mem(i, **kw):
    base = dict(id=f"mem_{i}", memory_type="semantic", key=f"key_{i}",
                content=f"内容 {i}", lane="general", status="active", tier="working")
    base.update(kw)
    return MemoryItem(**base)


# ── 纯函数：路由归一 / 分位数 / 采样判定 / 逾期判定 ──────────────────────
def test_normalize_route_masks_identifiers():
    assert normalize_route("/memory/mem_01J8ZK7Q9X2M4N6P8R0S1T2V3W", None) == "/memory/{id}"
    assert normalize_route("/memory/12345/rollback", None) == "/memory/{id}/rollback"
    assert normalize_route("/monitor/overview", None) == "/monitor/overview"
    # 框架给出模板时优先用模板
    assert normalize_route("/memory/whatever", "/memory/{memory_id}") == "/memory/{memory_id}"


def test_percentile_is_pure():
    assert percentile([], 0.95) == 0.0
    assert percentile([10.0], 0.95) == 10.0
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 0.5) == 50.5
    assert percentile(values, 0.95) == 95.05
    assert percentile(values, 1.0) == 100.0


def test_should_persist_sampling_rules():
    assert should_persist(500, 1.0, seen=1) is True          # 5xx 必留
    assert should_persist(404, 1.0, seen=2) is True          # 4xx 必留
    assert should_persist(200, 5000.0, seen=3) is True       # 慢请求必留
    assert should_persist(200, 1.0, seen=20, sample=20) is True   # 1/20 采样命中
    assert should_persist(200, 1.0, seen=21, sample=20) is False
    assert should_persist(200, 1.0, seen=20, sample=0) is False   # 采样关闭


def test_worker_staleness_thresholds():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    fresh = worker_staleness("ingest", period_seconds=3600,
                             last_run=now - timedelta(minutes=10), now=now)
    assert fresh["status"] == "ok" and fresh["overdue"] is False

    never = worker_staleness("digest", period_seconds=86400, last_run=None, now=now,
                             process_started_at=now - timedelta(minutes=1))
    assert never["status"] == "never" and never["overdue"] is False

    # 周期 1h + 宽限 15min：2 小时前跑过 → 逾期但不到 critical（2 个周期）
    late = worker_staleness("evolve", period_seconds=3600,
                            last_run=now - timedelta(hours=2), now=now)
    assert late["overdue"] is True and late["critical"] is False
    assert late["status"] == "overdue"

    dead = worker_staleness("evolve", period_seconds=3600,
                            last_run=now - timedelta(hours=5), now=now)
    assert dead["critical"] is True and dead["status"] == "critical"

    unknown = worker_staleness("reflect", period_seconds=86400, last_run=None, now=now)
    assert unknown["status"] == "unknown" and unknown["overdue"] is False


# ── 采集器 ────────────────────────────────────────────────────────────
def test_collector_window_endpoints_and_series():
    collector = MetricsCollector(buffer=100, bucket_minutes=10)
    now = 1_800_000_000.0
    for i in range(10):
        collector.record(method="POST", route="/add", status=200,
                         latency_ms=10.0 + i, ts=now - (10 - i))
    collector.record(method="POST", route="/add", status=500, latency_ms=900.0, ts=now - 1)
    collector.record(method="GET", route="/memory/{id}", status=404, latency_ms=3.0, ts=now - 1)

    window = collector.window(60, now=now)
    assert window["count"] == 12
    assert window["errors"] == 1 and window["client_errors"] == 1
    assert window["status_classes"] == {"2xx": 10, "5xx": 1, "4xx": 1}
    assert window["error_rate"] == pytest.approx(1 / 12, abs=1e-3)
    assert window["max_ms"] == 900.0
    assert window["p50_ms"] > 0

    endpoints = collector.endpoints(60, limit=10, now=now)
    assert endpoints[0]["route"] == "/add" and endpoints[0]["count"] == 11
    assert {row["route"] for row in endpoints} == {"/add", "/memory/{id}"}

    problems = collector.recent(10, only_problems=True)
    assert {row["status"] for row in problems} == {500, 404}

    series = collector.series(5, now=now)
    assert len(series) == 5
    assert sum(row["count"] for row in series) == 12   # 缺分钟补零但总数守恒
    assert max(row["count"] for row in series) == 12   # 全部落在同一分钟桶
    assert [row["minute"] for row in series] == sorted(row["minute"] for row in series)

    totals = collector.totals()
    assert totals["requests_total"] == 12 and totals["server_errors_total"] == 1


def test_collector_evicts_beyond_buffer():
    collector = MetricsCollector(buffer=5, bucket_minutes=10)
    now = 1_800_000_000.0
    for i in range(9):
        collector.record(method="GET", route="/health", status=200, latency_ms=1.0, ts=now)
    totals = collector.totals()
    assert totals["requests_total"] == 9
    assert totals["buffer_size"] == 5 and totals["evicted_total"] == 4


# ── 快照聚合（真实内存 SQLite，不 mock 内部逻辑）───────────────────────
def test_build_monitor_snapshot_aggregates_real_db(monitor_env):
    session_factory, _engine, collector = monitor_env
    now = utcnow()
    with session_factory() as s:
        s.add(_mem(1, lane="fact", status="active"))
        s.add(_mem(2, lane="fact", status="archived"))
        s.add(_mem(3, lane="chat", status="active"))
        s.add(MemoryCandidate(id="cand_1", document_id="doc_1", summary="待审",
                              status="pending_review"))
        s.add(SchedulerRun(name="ingest", last_run_utc=(now - timedelta(hours=5)).isoformat()))
        s.commit()

    collector.record(method="POST", route="/add", status=200, latency_ms=12.0)
    collector.record(method="POST", route="/add", status=503, latency_ms=1500.0)

    with session_factory() as s:
        snapshot = build_monitor_snapshot(s, now=now, include_quality=False)

    assert snapshot["memories"]["total"] == 3
    assert snapshot["memories"]["active"] == 2 and snapshot["memories"]["archived"] == 1
    assert snapshot["memories"]["by_lane"] == {"fact": 2, "chat": 1}
    assert snapshot["review"]["candidates_pending_review"] == 1
    assert snapshot["pipeline"]["candidates_pending_review"] == 1
    assert snapshot["storage"]["table_rows"]["memoryitem"] == 3
    assert snapshot["storage"]["table_rows"]["memory_fts"] == 0
    assert snapshot["process"]["pid"] > 0
    assert snapshot["requests"]["window"]["count"] == 2
    assert snapshot["requests"]["window"]["errors"] == 1

    workers = {w["name"]: w for w in snapshot["scheduler"]["workers"]}
    assert workers["ingest"]["last_run"] is not None
    assert workers["ingest"]["overdue"] is True          # 5 小时前跑过、周期 1h
    assert workers["ingest"]["critical"] is True
    assert workers["digest"]["status"] == "never"

    ids = {alert["id"] for alert in snapshot["alerts"]}
    assert "worker_overdue:ingest" in ids
    assert any(alert["severity"] == "critical" for alert in snapshot["alerts"])
    assert snapshot["summary"]["status"] == "critical"


def test_evaluate_alerts_is_a_pure_rule_set():
    healthy = {
        "scheduler": {"configured": True, "running": True, "workers": []},
        "requests": {"window_seconds": 900,
                     "window": {"count": 100, "errors": 0, "error_rate": 0.0, "p95_ms": 40.0}},
        "storage": {"database": {"mb": 10.0}},
        "pipeline": {"candidates_pending_review": 3},
        "security": {"loopback": True, "api_key_configured": True, "api_keys_total": 1},
        "dependency": {"llm": {"configured": True}},
        "quality": {},
    }
    assert evaluate_alerts(healthy) == []

    broken = {
        **healthy,
        "scheduler": {"configured": True, "running": False, "workers": [
            {"name": "forgetting", "overdue": True, "critical": True,
             "period_seconds": 86400, "last_run_age_seconds": 400000.0,
             "last_run": "2026-09-01T00:00:00+00:00"},
        ]},
        "requests": {"window_seconds": 900,
                     "window": {"count": 100, "errors": 30, "error_rate": 0.3,
                                "p95_ms": 9000.0}},
        "pipeline": {"candidates_pending_review": 500,
                     "candidates_pending_over_24h": 400,
                     "latest_reflect_run": {"id": "r1", "error": "LLM timeout"}},
        "security": {"loopback": False, "api_key_configured": False, "api_keys_total": 0},
        "dependency": {"llm": {"configured": False}},
        "quality": {"zero_recall_rate": 0.8, "real": 50, "zero": 40, "window_days": 7},
    }
    alerts = evaluate_alerts(broken)
    ids = {alert["id"] for alert in alerts}
    assert {"scheduler_not_running", "worker_overdue:forgetting", "reflect_failed",
            "high_error_rate", "slow_p95", "high_zero_recall", "candidate_backlog",
            "insecure_binding", "llm_key_missing"} <= ids
    # 严重项排在最前
    assert alerts[0]["severity"] == "critical"


def test_evaluate_alerts_flags_dev_fallback_auth():
    """配了 API_KEY 但 api_keys 表为空 = 实际无鉴权（dev 回退放行），必须报出来。"""
    base = {
        "scheduler": {"configured": True, "running": True, "workers": []},
        "requests": {"window_seconds": 900, "window": {"count": 0}},
        "storage": {"database": {"mb": 1.0}},
        "pipeline": {"candidates_pending_review": 0},
        "security": {"loopback": False, "api_key_configured": True, "api_keys_total": 0,
                     "effective_auth": "dev_fallback"},
        "dependency": {"llm": {"configured": True}},
        "quality": {},
    }
    ids = {alert["id"] for alert in evaluate_alerts(base)}
    assert "auth_dev_fallback" in ids

    signed = {**base, "security": {**base["security"], "api_keys_total": 2,
                                   "effective_auth": "bearer_table"}}
    assert "auth_dev_fallback" not in {alert["id"] for alert in evaluate_alerts(signed)}


def test_render_prometheus_exposes_snapshot(monitor_env):
    session_factory, _engine, collector = monitor_env
    with session_factory() as s:
        s.add(_mem(1, lane="fact"))
        s.commit()
    collector.record(method="POST", route="/add", status=200, latency_ms=20.0)
    with session_factory() as s:
        snapshot = build_monitor_snapshot(s, include_quality=False)
    text = render_prometheus(snapshot)

    assert text.count("# HELP lantai_memories_total") == 1
    assert "lantai_memories_total 1" in text
    assert 'lantai_memories_by_lane{lane="fact"} 1' in text
    assert "lantai_http_requests_total 1" in text
    assert "lantai_scheduler_running" in text
    assert text.endswith("\n")


# ── 路由与中间件（真实 HTTP 链路）──────────────────────────────────────
def test_monitor_routes_and_telemetry_roundtrip(monitor_env):
    from fastapi.testclient import TestClient

    from api_server import app

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/definitely-missing").status_code == 404

        overview = client.get("/monitor/overview?quality=false")
        assert overview.status_code == 200
        payload = overview.json()
        assert {"process", "storage", "memories", "pipeline", "scheduler",
                "requests", "security", "dependency", "alerts", "summary"} <= set(payload)
        # 中间件确实记到了内存指标（含 404）
        assert payload["requests"]["totals"]["requests_total"] >= 3
        assert payload["requests"]["window"]["client_errors"] >= 1
        routes = {row["route"] for row in payload["requests"]["endpoints"]}
        # 未匹配路由的 404 在指标里并成一桶（真实路径仍进 operation_logs）
        assert "(unmatched 404)" in routes

        # 落库：404 必留（错误优先），flush 后可查
        assert flush_telemetry() >= 1
        logs = client.get("/monitor/logs?only_problems=true").json()["items"]
        assert any(row["status_code"] == 404 for row in logs)
        assert all(row["problem"] for row in logs)

        series = client.get("/monitor/series?minutes=5").json()
        assert len(series["series"]) == 5
        assert sum(row["count"] for row in series["series"]) >= 3

        config = client.get("/monitor/config").json()["settings"]
        assert "监控" in config and config["监控"]["MONITOR_ENABLED"] is True

        prom = client.get("/monitor/prometheus")
        assert prom.status_code == 200
        assert "text/plain" in prom.headers["content-type"]
        assert "lantai_up 1" in prom.text

        # 未知 worker 不触发任何副作用，返回 422
        assert client.post("/monitor/workers/not-a-worker/run").status_code == 422


def test_unmatched_404s_collapse_in_metrics_but_keep_path_in_logs(monitor_env):
    """扫描器打来的随机 404 不能把端点排行打散；落库仍留真实路径供取证。"""
    from fastapi.testclient import TestClient

    from api_server import app

    with TestClient(app) as client:
        for i in range(3):
            assert client.get(f"/scanner-probe-{i}").status_code == 404
        overview = client.get("/monitor/overview?quality=false").json()
        routes = {row["route"]: row["count"] for row in overview["requests"]["endpoints"]}
        assert routes.get("(unmatched 404)") == 3
        assert not any(route.startswith("/scanner-probe-") for route in routes)

        flush_telemetry()
        logs = client.get("/monitor/logs?only_problems=true").json()["items"]
        assert {f"GET /scanner-probe-{i}" for i in range(3)} <= {
            row["endpoint"] for row in logs}


def test_list_operation_logs_validates_limit(monitor_env):
    with pytest.raises(ValueError):
        list_operation_logs(0)
    with pytest.raises(ValueError):
        list_operation_logs(501)
    assert list_operation_logs(10) == []


def test_safe_settings_view_masks_secrets(monkeypatch):
    from lantai.core.settings import settings

    monkeypatch.setattr(settings, "API_KEY", "sk-super-secret", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai-secret", raising=False)
    view = safe_settings_view()
    dumped = repr(view)
    assert "sk-super-secret" not in dumped
    assert "sk-openai-secret" not in dumped
    assert view["运行"]["API_KEY"] == "••••••"
    assert view["运行"]["DATABASE_URL"].startswith("sqlite:///")
    assert "/" not in view["运行"]["DATABASE_URL"].removeprefix("sqlite:///")
