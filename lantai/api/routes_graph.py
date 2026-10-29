"""记忆关系图路由（v0.9 MAP 星图数据服务，只读）。

借鉴 aiduMEI v18.3.0 MAP 面板数据服务（/knowledge/tree）的只读窄版：
兰台用自家 MemoryEdge + scene 归属，不引入作者的四类节点概念。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from lantai.core.auth import Principal, get_current_user
from lantai.ops.graph import get_graph, validate_graph_limit
from lantai.services.xuanjing_service import get_xuanjing_graph

router = APIRouter()


@router.get("/graph")
def graph_route(limit: int = 150, principal: Principal = Depends(get_current_user)) -> dict:
    """记忆关系星图（只读）：节点 + MemoryEdge 链接 + lane/relation 统计。"""
    try:
        validate_graph_limit(limit)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return get_graph(limit, principal=principal)


@router.get("/graph/xuanjing")
def xuanjing_graph_route(
    limit: int = Query(250, ge=1, le=1000, description="最大节点数"),
    scene_id: str | None = Query(None, description="限定场景 ID"),
    lane: str | None = Query(None, description="限定泳道"),
    include_archived: bool = Query(False, description="是否包含归档记忆"),
    principal: Principal = Depends(get_current_user),
) -> dict:
    """悬镜记忆星图（ADR-0059）：3D 神经元拓扑与力导向图数据聚合端点。"""
    return get_xuanjing_graph(
        limit=limit,
        scene_id=scene_id,
        lane=lane,
        include_archived=include_archived,
        principal=principal,
    )
