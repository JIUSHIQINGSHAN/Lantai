import time
from lantai.core.logger import logger
from lantai.core.scheduler import start_scheduler, stop_scheduler
from lantai.storage.db import engine
from sqlmodel import SQLModel
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

def main():
    logger.info("Initializing Database for worker...")
    SQLModel.metadata.create_all(engine)
    
    logger.info("Starting standalone worker (APScheduler)...")
    start_scheduler()
    
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down worker...")
        stop_scheduler()

if __name__ == "__main__":
    main()
