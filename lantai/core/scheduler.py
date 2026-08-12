from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from lantai.core.settings import settings
from lantai.core.logger import logger

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
                text("SELECT last_run_utc FROM scheduler_run WHERE name=:n"),
                params={"n": name}).first()
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
                text("INSERT INTO scheduler_run(name, last_run_utc) "
                     "VALUES(:n, :t) ON CONFLICT(name) "
                     "DO UPDATE SET last_run_utc=:t"),
                params={"n": name, "t": stamp})
            s.commit()
    except Exception:
        pass  # 落库失败不阻断（/stats 仍有内存态）


def get_last_run(name: str) -> str | None:
    """上次运行时间：DB 为准，内存兜底（首次重启前 DB 尚未写入时）。"""
    return _last_run_from_db(name) or WORKER_LAST_RUN.get(name)


def _parse_utc_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def should_catch_up(name: str, cron_hour: int, cron_minute: int = 0,
                    now: datetime | None = None,
                    last_run: str | None = None) -> bool:
    """每日 cron 任务漏跑判定（观察期保底）：上次运行早于最近一次已到点的
    调度时间 → 需补跑。未记录/无法解析按未跑处理（宁补跑不静默缺样本）。"""
    if last_run is None:
        last_run = get_last_run(name)
    now = now or datetime.now(timezone.utc)
    today_fire = now.replace(hour=cron_hour, minute=cron_minute,
                             second=0, microsecond=0)
    most_recent_fire = (today_fire if now >= today_fire
                        else today_fire - timedelta(days=1))
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
        jobs.append(("digest", run_digest_once, settings.DIGEST_CRON_HOUR,
                     _DIGEST_CRON_MINUTE))
    if settings.REFLECT_ENABLED:
        from lantai.workers.reflect_worker import run_reflect_once
        jobs.append(("reflect", run_reflect_once,
                     settings.REFLECT_CRON_HOUR, _REFLECT_CRON_MINUTE))
    for name, fn, hour, minute in jobs:
        if should_catch_up(name, hour, minute):
            run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
            _scheduler.add_job(fn, "date", run_date=run_at,
                               id=f"{name}_catchup", replace_existing=True)
            logger.info("启动补跑：%s 错过调度点，立即补跑一次", name)


def start_scheduler():
    global _scheduler
    from lantai.workers.ingest_worker import run_ingest_once
    from lantai.workers.evolve_worker import run_evolve_once
    from lantai.workers.forgetting_worker import run_forgetting_once

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_ingest_once, "interval",
                       minutes=settings.INGEST_CRON_MINUTES, id="ingest")
    _scheduler.add_job(run_evolve_once, "interval",
                       minutes=settings.EVOLVE_CRON_MINUTES, id="evolve")
    _scheduler.add_job(run_forgetting_once, "interval",
                       hours=settings.FORGET_CRON_HOURS, id="forget")
    # Ticket 02: 候选待审队列 TTL 归档
    from lantai.workers.digest_worker import run_candidate_ttl
    _scheduler.add_job(run_candidate_ttl, "interval",
                       hours=settings.CANDIDATE_TTL_CRON_HOURS,
                       id="candidate_ttl", replace_existing=True)

    # Ticket 03: Daily Digest 每日盘点报告（本地早晨；DIGEST_CRON_HOUR 为 UTC 小时）
    if settings.DIGEST_ENABLED:
        from lantai.workers.digest_worker import run_digest_once
        _scheduler.add_job(run_digest_once, "cron",
                           hour=settings.DIGEST_CRON_HOUR,
                           id="digest", replace_existing=True)

    # 参数建议（论文驱动优化·辅助模式）
    if settings.PARAM_ADVICE_ENABLED:
        from lantai.workers.param_advice_worker import run_param_advice_once
        from lantai.parameters.runtime import refresh_runtime_params
        _scheduler.add_job(run_param_advice_once, "interval",
                           minutes=settings.PARAM_ADVICE_CRON_MINUTES,
                           id="param_advice", replace_existing=True)
        # 跨进程参数热更新（DB 为事实源，进程内轮询）
        _scheduler.add_job(refresh_runtime_params, "interval",
                           seconds=settings.PARAM_OVERRIDE_REFRESH_SECONDS,
                           id="param_refresh", replace_existing=True)

    # Reflection 反思/蒸馏（spec: docs/plans/reflection-module-spec.md）
    if settings.REFLECT_ENABLED:
        from lantai.workers.reflect_worker import run_reflect_once
        _scheduler.add_job(run_reflect_once, "cron",
                           hour=settings.REFLECT_CRON_HOUR,
                           minute=_REFLECT_CRON_MINUTE,
                           id="reflect", replace_existing=True)

    # F7: coalesce idle flush（每 2 秒检查一次空闲缓冲；冲刷结果持久化，不静默丢弃）
    if settings.COALESCE_ENABLED:
        from lantai.ingestion.coalesce import get_coalesce_buffer
        from lantai.workers.ingest_worker import run_coalesce_idle
        _scheduler.add_job(run_coalesce_idle, "interval",
                           seconds=2, id="coalesce_idle", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started with ingest/evolve/forget jobs")
    _catch_up_daily_jobs()

def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)

