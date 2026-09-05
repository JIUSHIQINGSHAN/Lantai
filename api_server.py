from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends

from lantai.core.settings import settings
from lantai.core.logger import logger
from lantai.core.scheduler import start_scheduler, stop_scheduler
from lantai.core.auth import verify_api_key, assert_secure_binding
from lantai.core.acl import verify_agent
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
    routes_obsidian_router,
    routes_import_router,
    routes_tree_router,
    routes_crystals_router,
    routes_graph_router,
    routes_recall_chain_router,
    routes_ui_router,
    routes_work_items_router,
    routes_persona_router,
    routes_scratchpad_router,
    routes_probing_router,
    routes_terminal_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    concurrency = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    if concurrency > 1:
        logger.warning(
            "检测到 WEB_CONCURRENCY=%d > 1：当前版本调度器（APScheduler）与嵌入式 SQLite/ChromaDB "
            "按单进程模型设计。多 Worker 并发运行会导致遗忘/蒸馏定时任务重复执行及向量库写锁争用。"
            "生产环境强烈建议单进程部署（--workers 1）。", concurrency
        )
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


app = FastAPI(title="兰台记忆（Lantai）", version="0.21.0", lifespan=lifespan)

# 公共端点（不需要鉴权）
app.include_router(routes_health_router)
app.include_router(routes_ui_router)

# 业务端点（需要 API Key 鉴权）
CORE_ROUTERS = [
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_checkpoint_router,
    routes_evolution_router,
    routes_health_protected_router,
    routes_candidates_router,
    routes_dialogue_router,
    routes_digest_router,
    routes_conflicts_router,
    routes_import_router,
    routes_edges_router,
    routes_retrieval_router,
    routes_verification_router,
    routes_persona_router,
    routes_param_advice_router,
    routes_scratchpad_router,
    routes_probing_router,
    routes_recall_chain_router,
    routes_sources_router,
    routes_graph_router,
]

EXT_ROUTERS = {
    "obsidian": (settings.FEATURE_OBSIDIAN, routes_obsidian_router),
    "wiki": (settings.FEATURE_WIKI, routes_tree_router),
    "vision": (settings.FEATURE_VISION, None), 
    "kaogong": (settings.FEATURE_KAOGONG, None),
    "scenes": (settings.FEATURE_SCENES, None),
    "terminal": (settings.FEATURE_TERMINAL, routes_terminal_router),
    "work_items": (settings.FEATURE_WORK_ITEMS, routes_work_items_router),
    "crystals": (False, routes_crystals_router),
}

AUTH = [Depends(verify_api_key), Depends(verify_agent)]

for r in CORE_ROUTERS:
    app.include_router(r, dependencies=AUTH)

for name, (enabled, r) in EXT_ROUTERS.items():
    if enabled and r is not None:
        app.include_router(r, dependencies=AUTH, tags=[f"ext:{name}"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
