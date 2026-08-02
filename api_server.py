from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends

from remembrance.core.settings import settings
from remembrance.core.logger import logger
from remembrance.core.scheduler import start_scheduler, stop_scheduler
from remembrance.core.auth import verify_api_key
from remembrance.storage.db import init_db

from remembrance.api import (
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_checkpoint_router,
    routes_sources_router,
    routes_evolution_router,
    routes_health_router,
    routes_edges_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("Remembrance-System started on port %s", settings.PORT)
    yield
    stop_scheduler()


app = FastAPI(title="Remembrance-System", version="0.2.0", lifespan=lifespan)

# 公共端点（不需要鉴权）
app.include_router(routes_health_router)

# 业务端点（需要 API Key 鉴权）
protected_routers = [
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_checkpoint_router,
    routes_sources_router,
    routes_evolution_router,
    routes_edges_router,
]
for router in protected_routers:
    app.include_router(router, dependencies=[Depends(verify_api_key)])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
