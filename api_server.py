from contextlib import asynccontextmanager
from fastapi import FastAPI

from remembrance.core.settings import settings
from remembrance.core.logger import logger
from remembrance.core.scheduler import start_scheduler, stop_scheduler
from remembrance.storage.db import init_db

from remembrance.api import (
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_checkpoint_router,
    routes_sources_router,
    routes_evolution_router,
    routes_health_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("Remembrance-System started on port %s", settings.PORT)
    yield
    stop_scheduler()


app = FastAPI(title="Remembrance-System", version="0.2.0", lifespan=lifespan)

app.include_router(routes_health_router)
app.include_router(routes_memory_router)
app.include_router(routes_search_router)
app.include_router(routes_gate_router)
app.include_router(routes_checkpoint_router)
app.include_router(routes_sources_router)
app.include_router(routes_evolution_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
