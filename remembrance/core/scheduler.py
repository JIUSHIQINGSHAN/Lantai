from apscheduler.schedulers.background import BackgroundScheduler
from remembrance.core.settings import settings
from remembrance.core.logger import logger

_scheduler: BackgroundScheduler | None = None

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
    _scheduler.start()
    logger.info("Scheduler started with ingest/evolve/forget jobs")

def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)
