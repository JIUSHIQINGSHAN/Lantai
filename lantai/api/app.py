import math
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lantai.api import (
    routes_admin_router,
    routes_candidates_router,
    routes_catalog_router,
    routes_cehou_router,
    routes_checkpoint_router,
    routes_cognitive_router,
    routes_conflicts_router,
    routes_crystals_router,
    routes_dialogue_router,
    routes_diben_router,
    routes_digest_router,
    routes_dossiers_router,
    routes_edges_router,
    routes_evolution_router,
    routes_famen_router,
    routes_gate_router,
    routes_gouchen_router,
    routes_graph_router,
    routes_health_protected_router,
    routes_health_router,
    routes_import_router,
    routes_jiaochou_router,
    routes_kaogong_router,
    routes_memory_router,
    routes_mokan_router,
    routes_monitor_router,
    routes_niyi_router,
    routes_obsidian_router,
    routes_param_advice_router,
    routes_persona_router,
    routes_pisha_router,
    routes_probing_router,
    routes_prompts_router,
    routes_qijuzhu_router,
    routes_qingniao_router,
    routes_recall_chain_router,
    routes_retrieval_router,
    routes_sanxing_router,
    routes_scratchpad_router,
    routes_search_router,
    routes_shishi_router,
    routes_shiyi_router,
    routes_sources_router,
    routes_tanyi_router,
    routes_terminal_router,
    routes_tree_router,
    routes_ui_router,
    routes_verification_router,
    routes_work_items_router,
    routes_zhiming_router,
    routes_zhishu_router,
    routes_zuozheng_router,
)
from lantai.core.auth import assert_secure_binding, get_current_user
from lantai.core.logger import logger
from lantai.core.scheduler import start_scheduler, stop_scheduler
from lantai.core.settings import settings
from lantai.observability.telemetry import TelemetryMiddleware, get_writer
from lantai.storage.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    concurrency = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    if concurrency > 1:
        logger.warning(
            "检测到 WEB_CONCURRENCY=%d > 1：当前版本调度器（APScheduler）与嵌入式 SQLite/ChromaDB "
            "按单进程模型设计。多 Worker 并发运行会导致遗忘/蒸馏定时任务重复执行及向量库写锁争用。"
            "生产环境强烈建议单进程部署（--workers 1）。",
            concurrency,
        )
    assert_secure_binding()
    settings.validate_config()
    init_db()
    # v022 吸收补充迁移（票据 05）：retrieval_event.session_id（写线活性判据）
    from lantai.storage.db import engine as _db_engine
    from lantai.storage.migrations_v022 import apply_v022_migrations

    apply_v022_migrations(_db_engine)
    # 启动时加载 DB 参数 override（论文驱动优化的当前生效配置）
    from lantai.parameters.runtime import load_runtime_params_at_startup

    try:
        load_runtime_params_at_startup()
    except Exception:
        logger.exception("load runtime params at startup failed (keep defaults)")
    if settings.LANTAI_RUN_SCHEDULER:
        start_scheduler()
    # 司天（ADR-0044）：遥测落库后台线程（采样批量写 operation_logs，失败不影响请求）
    if settings.MONITOR_ENABLED:
        get_writer().start()
    logger.info("兰台记忆（Lantai） started on %s:%s", settings.HOST, settings.PORT)
    yield
    if settings.MONITOR_ENABLED:
        get_writer().stop()
    stop_scheduler()


def docs_kw() -> dict:
    """票 87：docs 三件套（/docs、/redoc、/openapi.json）默认关闭——生产公网面
    收敛，186 条路由完整契约与版本号不再随默认构造公开测绘（2026-11-09 黑盒
    审计病灶）。dev/本地调试显式设 LANTAI_DOCS_ENABLED=true 打开；/health 与
    /api/memory/health 活探针、/ui/*、鉴权链不受影响（回归锁
    tests/test_docs_surface_hardening.py）。
    """
    if os.environ.get("LANTAI_DOCS_ENABLED", "").lower() in ("1", "true", "yes"):
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def create_app() -> FastAPI:
    """应用工厂（票 87 形态重整）：构造一次成型，**不依赖模块级 env 快照**。

    reload 退役案：初版用模块级 `_DOCS_ENABLED` 快照 + 测试 importlib.reload
    切换两配置——实测连坐全量（129 个测试文件在 collection 期绑定旧 app 对象，
    reload 换掉 `lantai.api.app.app` 身份后，fixture 内 fresh import 的新对象与
    测试体的旧对象分叉，dependency_overrides 按**对象同一性**查表落空 → ACL
    滤空，test_ticket72 arm1b 全量红单飞绿实锚）。工厂形态下生产 app 只在本
    模块 import 时构造一次（永不 reload），测试要另一种配置直接调
    create_app() 造一次性实例，零模块状态搅动。
    """
    # 形状说明：title/version 必须与 FastAPI( 同行——release_check「FastAPI /docs
    # 版本」门禁正则（release_check.py:39）对本文件源码扫
    # `FastAPI\(title="...", version="..."`，拆行即版本一致性假红。行宽按
    # ruff 显示宽度口径（CJK 算 2 列）须 ≤100，局部名取短（docs_kw/app）
    # 即为压显示列数保单行，勿改名拉长。
    app = FastAPI(title="兰台记忆（Lantai）", version="0.68.0", lifespan=lifespan, **docs_kw())

    app.add_middleware(CognitiveMiddleware)
    # 司天（ADR-0044）：请求级遥测（内存指标 + 采样落库）；关闭时中间件零开销直通
    app.add_middleware(TelemetryMiddleware)

    # 票 70（UI 出口防护）：统一安全头——nosniff 防嗅探改读、DENY 防点击劫持
    # （控制台有裁决/删除/回滚按钮，可被第三方页 iframe 叠加诱导层）、CSP 防外
    # 注脚本执行。CSP 宽严按实测内联用量白名单化：routes_ui 内联页 11 个
    # script、index.html 40 处内联样式，script/style 需 'unsafe-inline'——仍拦
    # 外部脚本注入这一真实威胁（远端 script 会被 default-src 'self' 拒载）；
    # 不开 'unsafe-eval'。
    _UI_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )

    @app.middleware("http")
    async def _security_headers_middleware(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", _UI_CSP)
        return response

    # 422 渲染消毒（票 58）：FastAPI 的 422 响应体以 allow_nan=False 渲染（票 35
    # 红测实测），RequestValidationError 的 error input 携带 NaN/inf（宽松 JSON
    # 解析器产出的非标准字面量）时 422 自身渲染炸 500——验证错误被吞成服务器
    # 错误。此处把 error 载荷里的非有限 float 替换为 repr 字符串后照常 422，仅
    # 影响坏输入的报错形态，正常 422 逐字节不变。
    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_exception_handler(request, exc: RequestValidationError):
        def _replace_non_finite(obj):
            if isinstance(obj, float):
                return repr(obj) if not math.isfinite(obj) else obj
            if isinstance(obj, dict):
                return {k: _replace_non_finite(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_replace_non_finite(v) for v in obj]
            return obj

        return JSONResponse(
            status_code=422,
            content={"detail": _replace_non_finite(jsonable_encoder(exc.errors()))},
        )

    # 公共端点（不需要鉴权）
    app.include_router(routes_health_router)
    app.include_router(routes_ui_router)
    app.include_router(routes_admin_router)

    AUTH = [Depends(get_current_user)]
    for r in CORE_ROUTERS:
        app.include_router(r, dependencies=AUTH)
    for name, (enabled, r) in EXT_ROUTERS.items():
        if enabled and r is not None:
            app.include_router(r, dependencies=AUTH, tags=[f"ext:{name}"])

    return app


# 润物（ADR-0060）认知中间件 import 置于工厂定义前（工厂体内引用）
from lantai.runtime.middleware import CognitiveMiddleware  # noqa: E402

# 业务端点（需要 API Key 鉴权）——工厂装配表（调用 create_app() 时求值）
CORE_ROUTERS = [
    routes_cognitive_router,
    routes_memory_router,
    routes_search_router,
    routes_gate_router,
    routes_monitor_router,
    routes_checkpoint_router,
    routes_evolution_router,
    routes_health_protected_router,
    routes_candidates_router,
    routes_dialogue_router,
    routes_diben_router,
    routes_digest_router,
    routes_dossiers_router,
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
    routes_prompts_router,
    routes_graph_router,
    routes_tanyi_router,
    routes_zhishu_router,
    routes_catalog_router,
    routes_qijuzhu_router,
    routes_famen_router,
    routes_zuozheng_router,
    routes_shiyi_router,
    routes_jiaochou_router,
    routes_niyi_router,
    routes_diben_router,
    routes_cehou_router,
    routes_shishi_router,
    routes_qingniao_router,
    routes_pisha_router,
    routes_kaogong_router,
    routes_sanxing_router,
    routes_zhiming_router,
    routes_gouchen_router,
    routes_mokan_router,
]

EXT_ROUTERS = {
    "obsidian": (settings.FEATURE_OBSIDIAN, routes_obsidian_router),
    "wiki": (settings.FEATURE_WIKI, routes_tree_router),
    "vision": (settings.FEATURE_VISION, None),
    "kaogong": (settings.FEATURE_KAOGONG, None),
    "scenes": (settings.FEATURE_SCENES, None),
    "terminal": (settings.FEATURE_TERMINAL, routes_terminal_router),
    "work_items": (settings.FEATURE_WORK_ITEMS, routes_work_items_router),
    "crystals": (settings.FEATURE_CRYSTALS, routes_crystals_router),
}

# 生产单例：import 期构造一次，全仓 129+ 测试文件与 uvicorn 入口共享同一对象。
app = create_app()


def main() -> None:
    """`lantai-server` console script entrypoint."""
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


if __name__ == "__main__":
    main()
