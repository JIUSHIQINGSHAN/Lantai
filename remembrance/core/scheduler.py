from apscheduler.schedulers.background import BackgroundScheduler
from remembrance.core.settings import settings
from remembrance.core.logger import logger

_scheduler: BackgroundScheduler | None = None

# F8: worker 上次运行时间记录（供 /stats 暴露）
WORKER_LAST_RUN: dict[str, str] = {}


def record_run(name: str) -> None:
    from remembrance.core.time import utcnow
    WORKER_LAST_RUN[name] = utcnow().isoformat()

def start_scheduler():
    global _scheduler
    from remembrance.workers.ingest_worker import run_ingest_once
    from remembrance.workers.evolve_worker import run_evolve_once
    from remembrance.workers.forgetting_worker import run_forgetting_once

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_ingest_once, "interval",
                       minutes=settings.INGEST_CRON_MINUTES, id="ingest")
    _scheduler.add_job(run_evolve_once, "interval",
                       minutes=settings.EVOLVE_CRON_MINUTES, id="evolve")
    _scheduler.add_job(run_forgetting_once, "interval",
                       hours=settings.FORGET_CRON_HOURS, id="forget")

    # F7: coalesce idle flush（每 2 秒检查一次空闲缓冲）
    if settings.COALESCE_ENABLED:
        from remembrance.ingestion.coalesce import get_coalesce_buffer
        coalesce_buffer = get_coalesce_buffer()
        _scheduler.add_job(coalesce_buffer.check_idle, "interval",
                           seconds=2, id="coalesce_idle", replace_existing=True)

    _scheduler.start()
    logger.info("Scheduler started with ingest/evolve/forget jobs")

def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)
