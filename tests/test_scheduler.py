"""观察期保底：worker 上次运行时间落库（迁移 v8）+ 每日任务启动补跑

测试纪律（AGENTS.md）：不 mock 内部计算逻辑——纯判定函数直调；
record_run/get_last_run 用内存 SQLite 真实建表（patch db.engine，仅隔离存储）。
仅允许 mock 外部副作用：BackgroundScheduler（外部调度器对象）。
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy import text

import lantai.core.scheduler as scheduler_mod
import lantai.storage.db as db_module
from lantai.core.scheduler import (record_run, get_last_run, should_catch_up)
from lantai.models.tables import SchedulerRun


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


@pytest.fixture()
def sched_db(monkeypatch):
    """内存 SQLite 真实建表（含 scheduler_run）+ patch db.engine。"""
    import lantai.models.tables  # noqa: F401
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "get_session", lambda: Session(engine))
    monkeypatch.setitem(scheduler_mod.WORKER_LAST_RUN, "digest", "")
    monkeypatch.setitem(scheduler_mod.WORKER_LAST_RUN, "reflect", "")
    return engine


def _seed_run(engine, name: str, last_run_utc: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO scheduler_run(name, last_run_utc) "
                 "VALUES(:n, :t) ON CONFLICT(name) "
                 "DO UPDATE SET last_run_utc=:t"),
            {"n": name, "t": last_run_utc})


class TestShouldCatchUp:
    """纯判定：每日 cron 任务漏跑 → 补跑；已跑/未到点 → 不补。"""

    NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)  # 已过 06:00/06:01

    def test_none_last_run_catches_up(self):
        assert should_catch_up("digest", 22, 0, now=self.NOW, last_run=None)

    def test_run_after_most_recent_fire_skips(self):
        # 今天 06:00:30 已跑（> 今天 06:00 调度点）→ 不补
        last = (self.NOW.replace(hour=6, minute=0, second=30, microsecond=0)
                .isoformat())
        assert not should_catch_up("digest", 22, 0, now=self.NOW, last_run=last)

    def test_run_before_most_recent_fire_catches_up(self):
        # 上次运行是昨天 06:01，今天的 06:00 调度点已错过 → 补
        last = ((self.NOW - timedelta(days=1))
                .replace(hour=6, minute=1, second=0, microsecond=0)
                .isoformat())
        assert should_catch_up("digest", 22, 0, now=self.NOW, last_run=last)

    def test_before_todays_fire_with_todays_run_skips(self):
        # 10:00 UTC（未到今天 22:00 调度点），今早 06:01 已跑过 → 不补（避免双跑）
        now = datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc)
        last = (datetime(2026, 8, 11, 6, 1, 0, tzinfo=timezone.utc).isoformat())
        assert not should_catch_up("digest", 22, 0, now=now, last_run=last)

    def test_unparseable_last_run_catches_up(self):
        # 宁补跑不静默缺样本
        assert should_catch_up("reflect", 22, 1, now=self.NOW, last_run="garbage")


class TestRecordRunPersistence:
    """record_run 落库 + get_last_run 可跨会话读取（重启不丢）。"""

    def test_record_run_persists_across_sessions(self, sched_db):
        record_run("reflect")
        # 模拟重启：清空内存态后仍能从 DB 读到
        scheduler_mod.WORKER_LAST_RUN.pop("reflect", None)
        got = get_last_run("reflect")
        assert got is not None
        # 与写入时间一致（秒级）
        from lantai.core.time import utcnow
        assert abs((_utc(datetime.fromisoformat(got)) - utcnow())
                   .total_seconds()) < 5

    def test_record_run_updates_existing(self, sched_db):
        record_run("digest")
        first = get_last_run("digest")
        record_run("digest")
        second = get_last_run("digest")
        assert second is not None and first is not None
        assert second >= first


class TestStartSchedulerCatchup:
    """启动接线：错过调度点 → 注册补跑 job；已跑/未到点 → 不注册。"""

    class _FakeScheduler:
        def __init__(self, **kwargs):
            self.jobs = []

        def add_job(self, fn, trigger=None, **kwargs):
            self.jobs.append({"fn": fn, "trigger": trigger, **kwargs})

        def start(self):
            pass

    @pytest.fixture()
    def fake_scheduler(self, monkeypatch, sched_db):
        fake = self._FakeScheduler()
        monkeypatch.setattr(scheduler_mod, "BackgroundScheduler",
                            lambda **kw: fake)
        monkeypatch.setattr(scheduler_mod, "_scheduler", fake)
        monkeypatch.setattr(scheduler_mod.settings, "PARAM_ADVICE_ENABLED", False)
        return fake

    def test_stale_last_run_adds_catchup_jobs(self, fake_scheduler, sched_db):
        old = (datetime(2026, 8, 10, 6, 1, 0, tzinfo=timezone.utc).isoformat())
        _seed_run(sched_db, "digest", old)
        _seed_run(sched_db, "reflect", old)
        scheduler_mod.start_scheduler()
        ids = [j["id"] for j in fake_scheduler.jobs]
        assert "digest_catchup" in ids
        assert "reflect_catchup" in ids
        assert any(j["id"] == "digest_catchup" and j["trigger"] == "date"
                   for j in fake_scheduler.jobs)

    def test_fresh_last_run_skips_catchup(self, fake_scheduler, sched_db):
        fresh = datetime.now(timezone.utc).isoformat()
        _seed_run(sched_db, "digest", fresh)
        _seed_run(sched_db, "reflect", fresh)
        scheduler_mod.start_scheduler()
        ids = [j["id"] for j in fake_scheduler.jobs]
        assert "digest_catchup" not in ids
        assert "reflect_catchup" not in ids


class TestMigrationV8:
    """v7 库 → v8：scheduler_run 表创建 + user_version 记账。"""

    def test_v7_to_v8_creates_scheduler_run(self, tmp_path):
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "v7.db"))
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        from lantai.storage.db import apply_migrations
        apply_migrations(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "scheduler_run" in tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        conn.close()
