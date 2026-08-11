from apscheduler.schedulers.background import BackgroundScheduler
from lantai.core.settings import settings
from lantai.core.logger import logger

_scheduler: BackgroundScheduler | None = None

# F8: worker 上次运行时间记录（供 /stats 暴露）
WORKER_LAST_RUN: dict[str, str] = {}


def record_run(name: str) -> None:
    from lantai.core.time import utcnow
    WORKER_LAST_RUN[name] = utcnow().isoformat()

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

    # F7: coalesce idle flush（每 2 秒检查一次空闲缓冲；冲刷结果持久化，不静默丢弃）
    if settings.COALESCE_ENABLED:
        from lantai.ingestion.coalesce import get_coalesce_buffer
        from lantai.workers.ingest_worker import run_coalesce_idle
        _scheduler.add_job(run_coalesce_idle, "interval",
                           seconds=2, id="coalesce_idle", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started with ingest/evolve/forget jobs")

def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)
