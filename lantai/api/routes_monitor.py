"""司天（ADR-0044）：后台运行监控面板 REST 接口（只读为主）。

薄路由（ADR-0001 门面铁律）：聚合逻辑全部在 `lantai/ops/monitor.py`，
handler 只做参数校验与序列化。所有端点走 `get_current_user` 鉴权
（在 `api_server.CORE_ROUTERS` 注册），密钥类配置永不回传明文。

归属（票 `.scratch/readside-gaps/05`）：**上面那句"CORE_ROUTERS 注册即
鉴权"与事实不符**——`CORE_ROUTERS` 只决定挂不挂载，不把身份注入 handler。
五个 handler 原先一个 `Depends(get_current_user)` 都没有，于是任意持 key
者一次请求拿到宿主机绝对路径（含操作系统用户名）、python 完整版本串、
pid、OS 指纹、鉴权拓扑、全库记忆/待审计数、以及别人的请求日志。
现在每个 handler 取 `ctx=Depends(get_current_user)` 并下传 principal。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from lantai.core.auth import get_current_user
from lantai.ops.monitor import (
    get_monitor_snapshot,
    list_operation_logs,
    monitor_series,
    render_prometheus,
    safe_settings_view,
)
from lantai.services.worker_operation_service import run_worker

router = APIRouter(tags=["monitor"])


@router.get("/monitor/overview")
def monitor_overview(
    window: int = Query(None, ge=60, le=86400, description="请求统计窗口（秒）"),
    quality: bool = Query(True, description="是否附带零召回率等检索质量指标"),
    quality_days: int = Query(None, ge=1, le=365, description="检索质量窗口（天）"),
    ctx=Depends(get_current_user),
):
    """运行监控总览：进程/存储/记忆/管道/调度/请求/安全 + 规则告警。"""
    return get_monitor_snapshot(
        window_seconds=window,
        include_quality=quality,
        quality_window_days=quality_days,
        principal=ctx,
    )


@router.get("/monitor/series")
def monitor_series_route(minutes: int = Query(60, ge=1, le=1440), ctx=Depends(get_current_user)):
    """分钟级请求趋势（吞吐/错误/平均延迟），缺分钟补零。"""
    return {"minutes": minutes, "series": monitor_series(minutes)}


@router.get("/monitor/logs")
def monitor_logs(
    limit: int = Query(100, ge=1, le=500),
    only_problems: bool = Query(False, description="只看 4xx/5xx 与慢请求"),
    ctx=Depends(get_current_user),
):
    """`operation_logs` 落库遥测（采样保留：错误 + 慢请求 + 1/N 正常请求）。"""
    return {"items": list_operation_logs(limit, only_problems=only_problems, principal=ctx)}


@router.get("/monitor/config")
def monitor_config(ctx=Depends(get_current_user)):
    """当前生效运行配置（分组只读；密钥类打码）。"""
    return {"settings": safe_settings_view()}


@router.get("/monitor/prometheus", response_class=PlainTextResponse)
def monitor_prometheus(window: int = Query(None, ge=60, le=86400), ctx=Depends(get_current_user)):
    """Prometheus 文本格式（同一快照的另一种视图，便于外部监控抓取）。"""
    return PlainTextResponse(
        render_prometheus(get_monitor_snapshot(window_seconds=window, principal=ctx)),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.post("/monitor/workers/{worker_name}/run")
def monitor_run_worker(worker_name: str, ctx=Depends(get_current_user)):
    """手动触发一次 worker（与案牍共用 `worker_operation_service`，同名互斥）。"""
    try:
        return run_worker(worker_name)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
