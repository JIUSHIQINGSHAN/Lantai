"""调度线程恢复/健康回归：真实 APScheduler、SQLite 文件锁和 HTTP 路由。

仅隔离数据库与配置，不 mock 循环/调度/健康判定。SQLiteProbeStore 在真实内存
任务元数据推进后进行真实 SQLite 写入，把锁异常带到 APScheduler 主循环边界。
生产仅使用 MemoryJobStore；该探针验证额外容错，不能当作持久 store 的幂等保证。
"""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

import pytest
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_SUBMITTED
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler as UpstreamScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from lantai.api import routes_health
from lantai.core import scheduler as scheduler_mod
from lantai.core.settings import settings


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.01)
    assert predicate(), "condition did not become true before timeout"


class SQLiteProbeStore(MemoryJobStore):
    """真实存储 I/O 故障，不替换被测调度循环逻辑。"""

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.failed = Event()
        self.attempts = []

    def get_next_run_time(self):
        self.attempts.append(time.monotonic())
        try:
            with sqlite3.connect(self.path, timeout=0.01) as connection:
                connection.execute("UPDATE probe SET value=value+1")
        except sqlite3.OperationalError:
            self.failed.set()
            raise
        return super().get_next_run_time()


@pytest.fixture()
def lock_db(tmp_path):
    path = tmp_path / "lock.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.execute("INSERT INTO probe VALUES(0)")
    connection = sqlite3.connect(path)
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield path, connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture()
def schedulers(monkeypatch):
    """每个线程由本 fixture 关闭；测试中的短阈值不改产品出厂值。"""
    instances = []
    monkeypatch.setattr(scheduler_mod, "_scheduler", None)
    monkeypatch.setattr(settings, "LANTAI_RUN_SCHEDULER", True)

    def make(**kwargs):
        scheduler = scheduler_mod.BackgroundScheduler(timezone="UTC", **kwargs)
        scheduler.LOOP_HEARTBEAT_SECONDS = 0.03
        scheduler.LOOP_STALE_SECONDS = 0.25
        scheduler.RETRY_INITIAL_SECONDS = 0.04
        scheduler.RETRY_MAX_SECONDS = 0.08
        scheduler.MIN_LOOP_WAIT_SECONDS = 0.005
        instances.append(scheduler)
        monkeypatch.setattr(scheduler_mod, "_scheduler", scheduler)
        return scheduler

    yield make
    for scheduler in instances:
        if scheduler.running:
            scheduler.shutdown(wait=True)


@pytest.fixture()
def health_client():
    app = FastAPI()
    app.include_router(routes_health.router)
    app.include_router(routes_health.protected_router)
    saved_cache = routes_health._git_sha_cache
    try:
        with TestClient(app) as client:
            yield client
    finally:
        routes_health._git_sha_cache = saved_cache


def test_real_loop_survives_sqlite_lock_with_bounded_retry(lock_db, schedulers, caplog):
    path, connection = lock_db
    store = SQLiteProbeStore(path)
    scheduler = schedulers(jobstores={"default": store})
    executions = []
    scheduler.add_job(
        lambda: executions.append(time.monotonic()),
        "date",
        run_date=datetime.now(UTC),
        id="once",
    )
    scheduler.start()
    assert store.failed.wait(2), "real SQLite write must encounter the held lock"
    _wait_for(lambda: len(store.attempts) >= 3 or not scheduler._thread.is_alive())
    assert scheduler._thread.is_alive(), "SQLite OperationalError must not kill the scheduler"
    state = scheduler.loop_status()
    assert state["error_count"] >= 2
    assert state["consecutive_errors"] >= 2
    assert state["retry_delay_seconds"] <= scheduler.RETRY_MAX_SECONDS
    assert len(executions) == 1, "retry must not replay an already removed one-shot job"
    assert all(b - a >= 0.03 for a, b in zip(store.attempts, store.attempts[1:]))
    assert any("OperationalError" in record.getMessage() for record in caplog.records)

    connection.rollback()
    recovered = Event()
    scheduler.add_job(recovered.set, "date", id="after_lock")
    assert recovered.wait(3)
    _wait_for(lambda: scheduler.loop_status()["consecutive_errors"] == 0)
    assert scheduler.loop_status()["healthy"] is True
    assert len(executions) == 1


def test_persistent_failure_becomes_unhealthy_despite_live_thread(
    lock_db, schedulers, health_client
):
    path, _ = lock_db
    store = SQLiteProbeStore(path)
    scheduler = schedulers(jobstores={"default": store})
    scheduler.start()
    assert store.failed.wait(2)
    _wait_for(lambda: len(store.attempts) >= 5 or not scheduler._thread.is_alive())
    assert scheduler._thread.is_alive()
    _wait_for(lambda: scheduler.loop_status()["healthy"] is False)
    assert scheduler.loop_status()["reason"] == "stalled"
    assert health_client.get("/health").status_code == 503
    assert len(store.attempts) < 20, "persistent lock must not turn into a busy retry loop"


def test_idle_loop_makes_progress_and_respects_default_bounds(schedulers, health_client):
    scheduler = schedulers()
    scheduler.start()
    _wait_for(lambda: scheduler.loop_status()["last_progress_at"] is not None)
    first = scheduler.loop_status()["last_progress_at"]
    _wait_for(lambda: scheduler.loop_status()["last_progress_at"] != first)
    assert scheduler_mod.is_running() is True
    assert scheduler_mod.scheduler_health()["healthy"] is True
    assert health_client.get("/health").status_code == 200
    assert health_client.get("/api/memory/health").status_code == 200
    cls = type(scheduler)
    assert 0 < cls.MIN_LOOP_WAIT_SECONDS <= cls.RETRY_INITIAL_SECONDS
    assert cls.RETRY_INITIAL_SECONDS <= cls.RETRY_MAX_SECONDS < cls.LOOP_STALE_SECONDS
    assert cls.LOOP_HEARTBEAT_SECONDS < cls.LOOP_STALE_SECONDS


def test_dead_thread_cannot_hide_behind_running_flag(monkeypatch, health_client):
    """真实线程退出，APScheduler .running 仍 true（生产事故的状态形状）。"""

    class ExitedScheduler(UpstreamScheduler):
        def _main_loop(self):
            return

    scheduler = ExitedScheduler(timezone="UTC")
    monkeypatch.setattr(scheduler_mod, "_scheduler", scheduler)
    monkeypatch.setattr(settings, "LANTAI_RUN_SCHEDULER", True)
    scheduler.start()
    scheduler._thread.join(2)
    try:
        assert scheduler.running is True
        assert not scheduler._thread.is_alive()
        assert scheduler_mod.is_running() is False
        assert scheduler_mod.scheduler_status()["running"] is False
        for path in ("/health", "/api/memory/health"):
            response = health_client.get(path)
            assert response.status_code == 503
            assert response.json()["ok"] is False
    finally:
        scheduler.shutdown()


def test_blocked_jobstore_lock_does_not_block_health(schedulers, health_client):
    scheduler = schedulers()
    scheduler.start()
    _wait_for(lambda: scheduler.loop_status()["last_progress_at"] is not None)
    # 超时失败时先释放持有的锁，再关闭 executor，避免回归本身把测试进程卡死。
    with ThreadPoolExecutor(max_workers=1) as executor:
        with scheduler._jobstores_lock:
            scheduler.wakeup()
            _wait_for(lambda: scheduler.loop_status()["reason"] == "stalled")
            response = executor.submit(health_client.get, "/health").result(timeout=1)
            status = executor.submit(scheduler_mod.scheduler_status).result(timeout=1)
            assert response.status_code == 503
            assert status["thread_alive"] is True
            assert status["healthy"] is False
            assert status["jobs_available"] is False
    _wait_for(lambda: scheduler.loop_status()["healthy"] is True)


def test_wakeups_cannot_bypass_retry_backoff_and_shutdown_interrupts_it(lock_db, schedulers):
    path, _ = lock_db
    store = SQLiteProbeStore(path)
    scheduler = schedulers(jobstores={"default": store})
    scheduler.RETRY_INITIAL_SECONDS = 0.3
    scheduler.RETRY_MAX_SECONDS = 0.3
    scheduler.start()
    assert store.failed.wait(2)
    _wait_for(lambda: scheduler.loop_status()["consecutive_errors"] >= 1)
    stop_wakeups = Event()

    def wake_repeatedly():
        while not stop_wakeups.wait(0.005):
            scheduler.wakeup()

    thread = Thread(target=wake_repeatedly)
    thread.start()
    try:
        Event().wait(0.15)
        assert len(store.attempts) == 1
        started = time.monotonic()
        scheduler.shutdown(wait=True)
        assert time.monotonic() - started < 0.25
        assert scheduler.state == STATE_STOPPED
    finally:
        stop_wakeups.set()
        thread.join(1)


def test_disabled_scheduler_is_healthy_but_missing_enabled_scheduler_is_not(
    monkeypatch, health_client
):
    monkeypatch.setattr(scheduler_mod, "_scheduler", None)
    monkeypatch.setattr(settings, "LANTAI_RUN_SCHEDULER", False)
    assert scheduler_mod.scheduler_health()["reason"] == "disabled"
    assert health_client.get("/health").status_code == 200
    assert health_client.get("/api/memory/health").json()["ok"] is True
    monkeypatch.setattr(settings, "LANTAI_RUN_SCHEDULER", True)
    assert scheduler_mod.scheduler_health()["reason"] == "not_started"
    for path in ("/health", "/api/memory/health"):
        response = health_client.get(path)
        assert response.status_code == 503
        assert response.json()["ok"] is False
        assert "last_error" not in response.text  # 公共探针不泄内部错误/路径


def test_worker_sqlite_lock_does_not_repeat_scheduled_fire(lock_db, schedulers):
    """生产默认内存 store：worker 真遇 SQLite 锁，下一次计划继续且 fire 不重复。"""
    path, connection = lock_db
    scheduler = schedulers()
    failed, completed = Event(), Event()
    submissions = []

    def worker():
        with sqlite3.connect(path, timeout=0.01) as session:
            session.execute("UPDATE probe SET value=value+1")

    def observe(event):
        if event.code == EVENT_JOB_ERROR:
            failed.set()
        elif event.code == EVENT_JOB_EXECUTED:
            completed.set()
        elif event.code == EVENT_JOB_SUBMITTED:
            submissions.extend(event.scheduled_run_times)

    scheduler.add_listener(observe, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED | EVENT_JOB_SUBMITTED)
    scheduler.add_job(worker, "interval", seconds=0.08, id="writer", max_instances=1, coalesce=True)
    scheduler.start()
    assert failed.wait(2)
    assert scheduler._thread.is_alive()
    connection.rollback()
    assert completed.wait(2)
    scheduler.shutdown(wait=True)
    assert len(submissions) == len(set(submissions))
    assert len(submissions) < 15
    with sqlite3.connect(path) as session:
        assert session.execute("SELECT value FROM probe").fetchone()[0] > 0


def test_start_scheduler_uses_memory_store_and_retains_old_table(tmp_path, monkeypatch):
    """真实注册/启动/停止；旧 apscheduler_jobs 内容不用也不删除。"""
    from sqlmodel import create_engine

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE apscheduler_jobs(id TEXT PRIMARY KEY, next_run_time REAL, job_state BLOB)"
        )
        connection.execute("INSERT INTO apscheduler_jobs VALUES('legacy', NULL, X'1234')")
    engine = create_engine(f"sqlite:///{path}")
    # 旧版的 engine 只用于红测隔离；新版不再有该导入绑定。
    if hasattr(scheduler_mod, "engine"):
        monkeypatch.setattr(scheduler_mod, "engine", engine)
    monkeypatch.setattr(scheduler_mod, "_scheduler", None)
    for field in (
        "DIGEST_ENABLED",
        "REFLECT_ENABLED",
        "PARAM_ADVICE_ENABLED",
        "AUTODREAM_ENABLED",
        "COALESCE_ENABLED",
        "CONSOLIDATION_ENABLED",
    ):
        monkeypatch.setattr(settings, field, False)
    try:
        scheduler_mod.start_scheduler()
        scheduler = scheduler_mod._scheduler
        assert isinstance(scheduler._jobstores["default"], MemoryJobStore)
        assert scheduler._job_defaults["coalesce"] is True
        assert scheduler._job_defaults["max_instances"] == 1
        ids = {job.id for job in scheduler.get_jobs()}
        assert {"ingest", "evolve", "forget", "shadow_check", "receipt_miss"} <= ids
        scheduler_mod.stop_scheduler()
        scheduler_mod.start_scheduler()
        assert {job.id for job in scheduler_mod._scheduler.get_jobs()} == ids
    finally:
        scheduler_mod.stop_scheduler()
        engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM apscheduler_jobs").fetchall() == [
            ("legacy", None, b"\x12\x34")
        ]


def test_monitor_reports_thread_and_progress(schedulers, param_env):
    from lantai.ops.monitor import collect_scheduler_metrics, evaluate_alerts, render_prometheus

    _, engine = param_env
    scheduler = schedulers()
    scheduler.start()
    _wait_for(lambda: scheduler.loop_status()["last_progress_at"] is not None)
    with Session(engine) as session:
        status = collect_scheduler_metrics(session)
    assert status["thread_alive"] is True
    assert status["healthy"] is True
    assert status["last_progress_at"]
    with scheduler._jobstores_lock:
        scheduler.wakeup()
        _wait_for(lambda: scheduler.loop_status()["reason"] == "stalled")
        with Session(engine) as session:
            # 同线程重入不会挂住；health 的跨线程锁覆盖在上一个测试。
            stalled = collect_scheduler_metrics(session)
    snapshot = {"scheduler": stalled}
    assert "scheduler_unhealthy" in {row["id"] for row in evaluate_alerts(snapshot)}
    rendered = render_prometheus(snapshot)
    assert "lantai_scheduler_thread_alive 1" in rendered
    assert "lantai_scheduler_healthy 0" in rendered
    assert "lantai_scheduler_progress_age_seconds" in rendered
