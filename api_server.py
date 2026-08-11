from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends

from lantai.core.settings import settings
from lantai.core.logger import logger
from lantai.core.scheduler import start_scheduler, stop_scheduler
from lantai.core.auth import verify_api_key, assert_secure_binding
from lantai.storage.db import init_db

from lantai.api import (
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_checkpoint_router,
    routes_sources_router,
    routes_evolution_router,
    routes_health_router,
    routes_health_protected_router,
    routes_edges_router,
    routes_param_advice_router,
    routes_retrieval_router,
    routes_verification_router,
    routes_candidates_router,
    routes_dialogue_router,
    routes_digest_router,
    routes_conflicts_router,
    routes_conflicts_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_secure_binding()
    settings.validate_config()
    init_db()
    # 启动时加载 DB 参数 override（论文驱动优化的当前生效配置）
    from lantai.parameters.runtime import load_runtime_params_at_startup
    try:
        load_runtime_params_at_startup()
    except Exception:
        logger.exception("load runtime params at startup failed (keep defaults)")
    start_scheduler()
    logger.info("兰台记忆（Lantai） started on %s:%s", settings.HOST, settings.PORT)
    yield
    stop_scheduler()


app = FastAPI(title="兰台记忆（Lantai）", version="0.3.0", lifespan=lifespan)

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
    routes_health_protected_router,
    routes_param_advice_router,
    routes_retrieval_router,
    routes_verification_router,
    routes_candidates_router,
    routes_dialogue_router,
    routes_digest_router,
    routes_conflicts_router,
    routes_conflicts_router,
]
for router in protected_routers:
    app.include_router(router, dependencies=[Depends(verify_api_key)])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)

