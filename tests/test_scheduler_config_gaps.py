"""票 34：调度面配置缺口二项——consolidation cron 硬编码零开关、REFLECT
观察窗门槛从不评估。

病灶（立案基线 `5822840f`）：
1. consolidation 是同函数唯一既硬编码时区（`hour=19, minute=30`，注释自证
   「北京时间凌晨 03:30 / UTC 19:30」——只对东八区宿主成立）又无 ENABLED
   开关的定时作业；注入 `CONSOLIDATION_ENABLED=False` 关不掉（ADR-0002
   零硬编码口径破例点）。
2. `REFLECT_OBSERVATION_WINDOW_DAYS/REQUIRED_RUNS` 注释承诺的窗口门槛在
   lantai/ 内零消费者——worker 无条件跑，门槛从不评估（「配置显示有门槛、
   实际从不评估」中间态）。

拍板（票面 Answer，动手前记录）：
- consolidation → 补 `CONSOLIDATION_CRON_HOUR/MINUTE` + `CONSOLIDATION_ENABLED`
  （与 digest/reflect 同形，默认 19/30/True 行为零变化）。
- REFLECT → 工单 (a) 自闸 / (b) 删参数皆否（自闸死锁：合格运行日由运行
  本身产生，跳过则永不累积；ADR-0027 未撤不可删），取 (c) 评估点补齐：
  worker 开跑前真评察窗，回执透出 `observation` + `logger.info` 留痕，
  未毕业不跳过（采样器职责，CONTEXT.md 立法语义）。

六臂：
- consolidation 开关臂（红）：False 注入后作业清单仍含 consolidation。
- consolidation 时区参数化臂（红）：改 HOUR/MINUTE 后挂载点不变（19/30）。
- consolidation 默认形态锁（绿）：默认值挂载且读 settings（防默认写错）。
- REFLECT 评估透出臂（红）：窗口内合格日 < 门槛，真跑 worker 的回执必须
  含察窗评估且 graduated=False（现状回执无此键）。
- REFLECT 毕业臂（红）：合格日 ≥ 门槛 → graduated=True（现状同红）。
- 判据双锁臂（绿）：同种子下脚本（sqlite3 直读）与 worker（sqlmodel）
  评出同一察窗——两实现因连接形态分置，判据漂移由本臂钉死。

不 mock 被测链路：真 start_scheduler 注册逻辑（仅线程壳 BackgroundScheduler
类按仓内既有范式替换）、真 run_reflect_once、真 collect_observation_status，
仅隔离 DB。
"""

import importlib.util
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401  注册全部表
import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import ReflectRun

_BEIJING_STYLE_HOUR = 19  # 现状硬编码值（病灶 A 的证据值）
_BEIJING_STYLE_MINUTE = 30


# ── 工装 ──


@pytest.fixture()
def sched_env(tmp_path, monkeypatch):
    """内存 SQLite + 真注册工装：BackgroundScheduler 类按仓内既有范式
    （tests/test_receipt_miss_scrub.py:126）替换为记录壳——注册逻辑
    start_scheduler() 真跑，只有线程壳被替换。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)

    import lantai.core.scheduler as scheduler_mod

    added: list[dict] = []

    class _JobStub:
        def __init__(self, job):
            self.id = job["id"]
            self.trigger = job["trigger"]
            self.next_run_time = None  # 挂载态断言只看清单成员，不看触发时刻

    class _FakeScheduler:
        def __init__(self, **kwargs):
            self.jobs = added

        def add_job(self, fn, trigger=None, **kwargs):
            self.jobs.append({"id": kwargs.get("id"), "trigger": trigger, "kwargs": kwargs, "fn": fn})

        def get_jobs(self):
            return [_JobStub(j) for j in added]

        def start(self):
            pass

    monkeypatch.setattr(scheduler_mod, "BackgroundScheduler", lambda **kw: _FakeScheduler(**kw))
    monkeypatch.setattr(scheduler_mod, "_scheduler", None)
    return scheduler_mod, session_factory


def _mount(scheduler_mod):
    scheduler_mod.start_scheduler()
    return scheduler_mod.scheduler_status()


def _seed_reflect_run(session_factory, *, id: str, run_at: datetime, source: str = "scheduled") -> None:
    with session_factory() as s:
        s.add(
            ReflectRun(
                id=id,
                run_at=run_at,
                source=source,
                proposals_created=1,
            )
        )
        s.commit()


# ── consolidation：开关与时区参数化（病灶 A）──


def test_consolidation_switch_gates_mounting(sched_env, monkeypatch):
    """开关臂（红）：CONSOLIDATION_ENABLED=False 注入后，scheduler_status()
    作业清单不得含 consolidation——现状恒挂载，注入关不掉。"""
    scheduler_mod, _ = sched_env
    monkeypatch.setattr(settings, "CONSOLIDATION_ENABLED", False, raising=False)

    status = _mount(scheduler_mod)
    job_ids = [j["id"] for j in status["jobs"]]
    assert "consolidation" not in job_ids, (
        f"CONSOLIDATION_ENABLED=False 后 consolidation 仍被挂载——零开关病灶"
        f"（票 34 病灶 A）；job_ids={job_ids}"
    )


def test_consolidation_cron_reads_settings(sched_env, monkeypatch):
    """时区参数化臂（红）：CONSOLIDATION_CRON_HOUR/MINUTE 改值后挂载点随
    settings 变化——现状硬编码 19/30（东八区宿主语义）。"""
    scheduler_mod, _ = sched_env
    monkeypatch.setattr(settings, "CONSOLIDATION_CRON_HOUR", 3, raising=False)
    monkeypatch.setattr(settings, "CONSOLIDATION_CRON_MINUTE", 7, raising=False)

    scheduler_mod.start_scheduler()
    job = next(j for j in scheduler_mod._scheduler.jobs if j["id"] == "consolidation")
    assert job["kwargs"].get("hour") == 3 and job["kwargs"].get("minute") == 7, (
        f"consolidation cron 不随 settings 变化——hour={job['kwargs'].get('hour')}"
        f" minute={job['kwargs'].get('minute')}，硬编码（票 34 病灶 A）"
    )


def test_consolidation_default_true_mounts(sched_env):
    """默认形态锁（绿）：默认 CONSOLIDATION_ENABLED=True 时正常挂载，且
    默认值读 settings（19/30，与现状行为一致——防开关默认值写错）。"""
    scheduler_mod, _ = sched_env

    status = _mount(scheduler_mod)
    job_ids = [j["id"] for j in status["jobs"]]
    assert "consolidation" in job_ids, f"默认开关下 consolidation 未挂载；job_ids={job_ids}"
    job = next(j for j in scheduler_mod._scheduler.jobs if j["id"] == "consolidation")
    assert job["kwargs"].get("hour") == settings.CONSOLIDATION_CRON_HOUR, (
        "挂载点必须读 settings.CONSOLIDATION_CRON_HOUR（ADR-0002 单一真源）"
    )
    assert job["kwargs"].get("minute") == settings.CONSOLIDATION_CRON_MINUTE
    assert settings.CONSOLIDATION_CRON_HOUR == _BEIJING_STYLE_HOUR, (
        "默认值须与现状硬编码一致（行为零变化）"
    )
    assert settings.CONSOLIDATION_CRON_MINUTE == _BEIJING_STYLE_MINUTE


# ── REFLECT 观察窗：评估点补齐（病灶 B，拍板 (c)）──


def _seed_window(session_factory, qualified_days: int, *, base: datetime | None = None):
    """种 qualified_days 个合格 scheduled 运行日（逐日一条，UTC 日键）。"""
    ref = base or datetime.now(UTC)
    for i in range(qualified_days):
        _seed_reflect_run(
            session_factory,
            id=f"rr_ok_{i}",
            run_at=(ref - timedelta(days=i + 1)).replace(microsecond=0),
        )


def test_reflect_worker_surfaces_observation_when_below_threshold(
    sched_env, caplog
):
    """评估透出臂（红）：窗口内合格运行日（3）< 门槛（7）时，真跑 worker
    的回执必须含察窗评估（graduated=False）且 logger 留痕——现状回执无
    observation 键、门槛从不评估。

    未毕业不跳过（拍板 (c)：采样器职责，跳过即死锁）——回执其余键照常。"""
    scheduler_mod, session_factory = sched_env
    _seed_window(session_factory, qualified_days=3)

    from lantai.workers.reflect_worker import run_reflect_once

    with caplog.at_level(logging.INFO, logger="lantai"):
        res = run_reflect_once()

    assert "observation" in res, (
        f"worker 回执缺察窗评估——REFLECT_OBSERVATION_* 两参数零消费者，"
        f"门槛从不评估（票 34 病灶 B）；回执键={sorted(res)}"
    )
    obs = res["observation"]
    assert obs["graduated"] is False, f"3/7 合格日不应毕业；observation={obs}"
    assert obs["required_runs"] == settings.REFLECT_OBSERVATION_REQUIRED_RUNS
    assert obs["window_days"] == settings.REFLECT_OBSERVATION_WINDOW_DAYS
    assert any("察窗" in r.message for r in caplog.records), (
        "察窗状态必须 logger.info 留痕（宁 visible 不静默）"
    )


def test_reflect_worker_graduated_when_threshold_met(sched_env):
    """毕业臂（红）：窗口内合格运行日 ≥ 门槛 → graduated=True（现状回执
    无 observation 键，同红）。"""
    _, session_factory = sched_env
    _seed_window(session_factory, qualified_days=7)

    from lantai.workers.reflect_worker import run_reflect_once

    res = run_reflect_once()
    assert res["observation"]["graduated"] is True, (
        f"7/7 合格日应毕业；observation={res.get('observation')}"
    )


def test_observation_criteria_parity_script_vs_worker(sched_env):
    """判据双锁臂（绿）：同种子下脚本（sqlite3 直读，ADR-0027 判据）与
    worker（sqlmodel）评出同一察窗——两实现因连接形态分置，判据漂移
    由本臂钉死。

    种子：5 个合格日 + 两个独立日各一条不合格形态（manual 源 / 异常行）
    ——判据若错把 manual 或异常行算合格，qualified_days 变 7，断言即翻。"""
    _, session_factory = sched_env
    reference = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    ref_date = reference.date()

    qualified_dates = [ref_date - timedelta(days=i + 1) for i in range(5)]
    for i, day in enumerate(qualified_dates):
        _seed_reflect_run(
            session_factory,
            id=f"rr_ok_{i}",
            run_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
        )
    manual_day = ref_date - timedelta(days=6)
    _seed_reflect_run(
        session_factory,
        id="rr_manual",
        run_at=datetime.combine(manual_day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1),
        source="manual",
    )
    errored_day = ref_date - timedelta(days=7)
    with session_factory() as s:
        s.add(
            ReflectRun(
                id="rr_errored",
                run_at=datetime.combine(errored_day, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=1),
                source="scheduled",
                error="boom",
            )
        )
        s.commit()

    # worker 侧（sqlmodel 链内评估）
    from lantai.workers.reflect_worker import observation_status

    with session_factory() as s:
        worker_obs = observation_status(s, reference=reference)

    # 脚本侧（sqlite3 直读，同一列种子灌进独立内存库）
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reflect_run (run_at TEXT, source TEXT, curate_failed INT, "
        "rejecter_failed INT, error TEXT)"
    )
    script_rows = [
        (datetime.combine(day, datetime.min.time(), tzinfo=UTC).isoformat(), "scheduled", 0, 0, None)
        for day in qualified_dates
    ]
    script_rows.append(
        (
            (
                datetime.combine(manual_day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1)
            ).isoformat(),
            "manual",
            0,
            0,
            None,
        )
    )
    script_rows.append(
        (
            (
                datetime.combine(errored_day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=1)
            ).isoformat(),
            "scheduled",
            0,
            0,
            "boom",
        )
    )
    conn.executemany(
        "INSERT INTO reflect_run (run_at, source, curate_failed, rejecter_failed, error) "
        "VALUES (?, ?, ?, ?, ?)",
        script_rows,
    )
    conn.commit()

    spec = importlib.util.spec_from_file_location(
        "reflect_observation_status",
        Path(__file__).resolve().parent.parent / "scripts" / "reflect_observation_status.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    script_obs = mod.collect_observation_status(
        conn,
        required_runs=settings.REFLECT_OBSERVATION_REQUIRED_RUNS,
        window_days=settings.REFLECT_OBSERVATION_WINDOW_DAYS,
        reference_date=ref_date,
    )

    assert worker_obs["qualified_days"] == script_obs["qualified_days"] == 5, (
        f"worker/脚本察窗判据分叉：worker={worker_obs['qualified_days']} "
        f"script={script_obs['qualified_days']}（ADR-0027 四判据 + 日级 OR）"
    )
    assert worker_obs["graduated"] is False and script_obs["ready"] is False
