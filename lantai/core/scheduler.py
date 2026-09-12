from datetime import UTC, datetime, timedelta

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.storage.db import engine

_scheduler: BackgroundScheduler | None = None

# F8: worker 上次运行时间记录（供 /stats 暴露；观察期保底 v8 起同时落库持久化）
WORKER_LAST_RUN: dict[str, str] = {}

# 每日 cron 任务分钟位（启动补跑与 cron 注册共用一处，改调度点不用同步改两处）
_DIGEST_CRON_MINUTE = 0
_REFLECT_CRON_MINUTE = 1


def _last_run_from_db(name: str) -> str | None:
    """读 DB 持久化的上次运行时间；异常降级返回 None（不影响运行）。"""
    try:
        from sqlalchemy import text

        from lantai.storage.db import get_session

        with get_session() as s:
            row = s.exec(
                text("SELECT last_run_utc FROM scheduler_run WHERE name=:n"), params={"n": name}
            ).first()
            return row[0] if row else None
    except Exception:
        return None


def record_run(name: str) -> None:
    """记录 worker 本次运行完成时间：内存（/stats 即时）+ DB（重启不丢）。"""
    from lantai.core.time import utcnow

    stamp = utcnow().isoformat()
    WORKER_LAST_RUN[name] = stamp
    try:
        from sqlalchemy import text

        from lantai.storage.db import get_session

        with get_session() as s:
            s.exec(
                text(
                    "INSERT INTO scheduler_run(name, last_run_utc) "
                    "VALUES(:n, :t) ON CONFLICT(name) "
                    "DO UPDATE SET last_run_utc=:t"
                ),
                params={"n": name, "t": stamp},
            )
            s.commit()
    except Exception:
        pass  # 落库失败不阻断（/stats 仍有内存态）


def get_last_run(name: str) -> str | None:
    """上次运行时间：DB 为准，内存兜底（首次重启前 DB 尚未写入时）。"""
    return _last_run_from_db(name) or WORKER_LAST_RUN.get(name)


def is_running() -> bool:
    """调度器是否已启动（司天监控面板用；测试进程通常为 False）。"""
    return bool(_scheduler is not None and _scheduler.running)


def scheduler_status() -> dict:
    """调度器运行态与作业清单（只读；未启动时 running=False、jobs=[]）。

    司天（ADR-0044）监控面板的数据源之一：APScheduler 作业的真实下一次触发时间
    只有调度器自己知道，DB 里的 `scheduler_run` 只回答「上次跑没跑」。
    """
    if _scheduler is None:
        return {
            "running": False,
            "configured": bool(settings.LANTAI_RUN_SCHEDULER),
            "job_count": 0,
            "jobs": [],
        }
    jobs = []
    now = datetime.now(UTC)
    try:
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            if next_run is not None and next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            jobs.append(
                {
                    "id": job.id,
                    "name": getattr(job, "name", job.id),
                    "trigger": str(job.trigger),
                    "next_run_time": next_run.isoformat() if next_run else None,
                    "next_run_in_seconds": (
                        round((next_run - now).total_seconds(), 1) if next_run else None
                    ),
                    "paused": next_run is None,
                }
            )
    except Exception:
        logger.exception("读取调度器作业清单失败（降级为空清单）")
    jobs.sort(
        key=lambda row: (
            row["next_run_in_seconds"] is None,
            row["next_run_in_seconds"] or 0,
            row["id"],
        )
    )
    return {
        "running": bool(_scheduler.running),
        "configured": bool(settings.LANTAI_RUN_SCHEDULER),
        "job_count": len(jobs),
        "jobs": jobs,
    }


def worker_staleness(
    name: str,
    *,
    period_seconds: int,
    last_run: datetime | None,
    now: datetime | None = None,
    process_started_at: datetime | None = None,
    grace_factor: float | None = None,
    critical_factor: float | None = None,
) -> dict:
    """worker 逾期判定（纯函数，司天与案牍共用同一口径）。

    规则（与案牍 `runtime_status` 投影一致）：
    - 基线 = 上次运行时间，缺失则退到进程启动时间（宁保守不误报）；
    - 宽限 = max(周期 × grace_factor, 15 分钟)，吸收调度抖动；
    - 超过基线 + 周期 + 宽限 = overdue；超过 critical_factor 个完整周期 = critical。
    """
    from lantai.core.time import utcnow

    grace_ratio = (
        float(settings.MONITOR_WORKER_GRACE_FACTOR) if grace_factor is None else float(grace_factor)
    )
    critical_ratio = (
        float(settings.MONITOR_WORKER_CRITICAL_FACTOR)
        if critical_factor is None
        else float(critical_factor)
    )
    now = now or utcnow()
    period = timedelta(seconds=max(1, int(period_seconds)))
    grace = max(period * grace_ratio, timedelta(minutes=15))
    baseline = last_run or process_started_at
    if baseline is None:
        return {
            "name": name,
            "period_seconds": int(period.total_seconds()),
            "last_run": None,
            "last_run_age_seconds": None,
            "baseline": None,
            "due_at": None,
            "overdue": False,
            "overdue_seconds": 0.0,
            "critical": False,
            "status": "unknown",
        }
    due = baseline + period + grace
    elapsed = now - baseline
    overdue = now > due
    critical = bool(overdue and elapsed > period * critical_ratio)
    if not overdue:
        status = "ok" if last_run is not None else "never"
    else:
        status = "critical" if critical else "overdue"
    return {
        "name": name,
        "period_seconds": int(period.total_seconds()),
        "last_run": last_run.isoformat() if last_run else None,
        "last_run_age_seconds": (
            round((last_run - now).total_seconds() * -1, 1) if last_run else None
        ),
        "baseline": baseline.isoformat(),
        "due_at": due.isoformat(),
        "overdue": bool(overdue),
        "overdue_seconds": round(max(0.0, (now - due).total_seconds()), 1),
        "critical": critical,
        "status": status,
    }


def _parse_utc_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def should_catch_up(
    name: str,
    cron_hour: int,
    cron_minute: int = 0,
    now: datetime | None = None,
    last_run: str | None = None,
) -> bool:
    """每日 cron 任务漏跑判定（观察期保底）：上次运行早于最近一次已到点的
    调度时间 → 需补跑。未记录/无法解析按未跑处理（宁补跑不静默缺样本）。"""
    if last_run is None:
        last_run = get_last_run(name)
    now = now or datetime.now(UTC)
    today_fire = now.replace(hour=cron_hour, minute=cron_minute, second=0, microsecond=0)
    most_recent_fire = today_fire if now >= today_fire else today_fire - timedelta(days=1)
    if last_run is None:
        return True
    last_dt = _parse_utc_iso(last_run)
    if last_dt is None:
        return True
    return last_dt < most_recent_fire


def _catch_up_daily_jobs() -> None:
    """启动补跑：每日 cron 任务错过调度点（进程当时未运行）时补跑一次，
    观察期样本不因重启/关机断档。"""
    if _scheduler is None:
        return
    jobs = []
    if settings.DIGEST_ENABLED:
        from lantai.workers.digest_worker import run_digest_once

        jobs.append(("digest", run_digest_once, settings.DIGEST_CRON_HOUR, _DIGEST_CRON_MINUTE))
    if settings.REFLECT_ENABLED:
        from lantai.workers.reflect_worker import run_reflect_once

        jobs.append(("reflect", run_reflect_once, settings.REFLECT_CRON_HOUR, _REFLECT_CRON_MINUTE))
    for name, fn, hour, minute in jobs:
        if should_catch_up(name, hour, minute):
            run_at = datetime.now(UTC) + timedelta(seconds=2)
            _scheduler.add_job(
                fn, "date", run_date=run_at, id=f"{name}_catchup", replace_existing=True
            )
            logger.info("启动补跑：%s 错过调度点，立即补跑一次", name)


def start_scheduler():
    global _scheduler
    from lantai.workers.evolve_worker import run_evolve_once
    from lantai.workers.forgetting_worker import run_forgetting_once
    from lantai.workers.ingest_worker import run_ingest_once

    _scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs")},
        timezone="UTC",
    )
    # replace_existing=True 是必需的：jobstore 是 SQLAlchemyJobStore（持久化在同一个
    # SQLite 库里），第二次启动时旧作业仍在表中——缺这个参数会让 start() 抛
    # ConflictingIdError，服务对已存在的库再也起不来（只能删库或手工清表）。
    _scheduler.add_job(
        run_ingest_once,
        "interval",
        minutes=settings.INGEST_CRON_MINUTES,
        id="ingest",
        replace_existing=True,
    )
    _scheduler.add_job(
        run_evolve_once,
        "interval",
        minutes=settings.EVOLVE_CRON_MINUTES,
        id="evolve",
        replace_existing=True,
    )
    _scheduler.add_job(
        run_forgetting_once,
        "interval",
        hours=settings.FORGET_CRON_HOURS,
        id="forget",
        replace_existing=True,
    )
    # Ticket 02: 候选待审队列 TTL 归档
    from lantai.workers.digest_worker import run_candidate_ttl

    _scheduler.add_job(
        run_candidate_ttl,
        "interval",
        hours=settings.CANDIDATE_TTL_CRON_HOURS,
        id="candidate_ttl",
        replace_existing=True,
    )

    # Ticket 03: Daily Digest 每日盘点报告（本地早晨；DIGEST_CRON_HOUR 为 UTC 小时）
    if settings.DIGEST_ENABLED:
        from lantai.workers.digest_worker import run_digest_once

        _scheduler.add_job(
            run_digest_once,
            "cron",
            hour=settings.DIGEST_CRON_HOUR,
            id="digest",
            replace_existing=True,
        )

    # 参数建议（论文驱动优化·辅助模式）
    if settings.PARAM_ADVICE_ENABLED:
        from lantai.parameters.runtime import refresh_runtime_params
        from lantai.workers.param_advice_worker import run_param_advice_once

        _scheduler.add_job(
            run_param_advice_once,
            "interval",
            minutes=settings.PARAM_ADVICE_CRON_MINUTES,
            id="param_advice",
            replace_existing=True,
        )
        # 跨进程参数热更新（DB 为事实源，进程内轮询）
        _scheduler.add_job(
            refresh_runtime_params,
            "interval",
            seconds=settings.PARAM_OVERRIDE_REFRESH_SECONDS,
            id="param_refresh",
            replace_existing=True,
        )

    # Reflection 反思/蒸馏（spec: docs/plans/reflection-module-spec.md）
    if settings.REFLECT_ENABLED:
        from lantai.workers.reflect_worker import run_reflect_once

        _scheduler.add_job(
            run_reflect_once,
            "cron",
            hour=settings.REFLECT_CRON_HOUR,
            minute=_REFLECT_CRON_MINUTE,
            id="reflect",
            replace_existing=True,
        )

    # Fog: autodream 7 天周期蒸馏（后台合成 → 待审提案，人工闸门；不自动应用）
    if settings.AUTODREAM_ENABLED:
        from lantai.workers.autodream_worker import run_autodream_scheduled

        _scheduler.add_job(
            run_autodream_scheduled,
            "interval",
            days=settings.AUTODREAM_CRON_DAYS,
            id="autodream",
            replace_existing=True,
        )

    # 沉潜（ADR-0036）：每日夜梦沉淀与折叠压缩（北京时间凌晨 03:30 / UTC 19:30）
    from lantai.services.consolidation_service import run_consolidation_cycle

    _scheduler.add_job(
        run_consolidation_cycle,
        "cron",
        hour=19,
        minute=30,
        id="consolidation",
        replace_existing=True,
    )

    # F7: coalesce idle flush（每 2 秒检查一次空闲缓冲；冲刷结果持久化，不静默丢弃）
    if settings.COALESCE_ENABLED:
        from lantai.workers.ingest_worker import run_coalesce_idle

        _scheduler.add_job(
            run_coalesce_idle, "interval", seconds=2, id="coalesce_idle", replace_existing=True
        )

    _scheduler.start()
    logger.info("Scheduler started with ingest/evolve/forget jobs")
    _catch_up_daily_jobs()


def stop_scheduler():
    """关闭调度器（幂等）：已停止/未启动都不抛，避免 shutdown 路径连带炸掉。"""
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("调度器关闭异常（忽略，不阻断退出）")
    finally:
        _scheduler = None
