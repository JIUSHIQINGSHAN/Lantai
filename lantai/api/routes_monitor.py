"""瞭望（后台监控面板）路由：只读监控聚合端点。

- GET /monitor/snapshot   面板全量快照（健康/队列/worker/检索/吞吐/存储/运行时）
- GET /monitor/health     轻量健康（总体状态 + 告警，供轮询/外部探针）

只读，不改变任何系统状态；worker 手动触发仍走 POST /workers/{name}/run。
"""
from fastapi import APIRouter

from lantai.ops.monitor import get_monitor_snapshot

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/snapshot")
def monitor_snapshot() -> dict:
    """监控面板全量快照（只读聚合，单次请求）。"""
    return get_monitor_snapshot()


@router.get("/health")
def monitor_health() -> dict:
    """轻量健康视图：总体状态 + 告警列表（仪表盘红灯/外部探针用）。"""
    snap = get_monitor_snapshot()
    return {
        "ok": snap["health"]["overall"] == "ok",
        "overall": snap["health"]["overall"],
        "alerts": snap["alerts"],
        "generated_at": snap["generated_at"],
    }
