"""记忆关系图路由（v0.9 MAP 星图数据服务，只读）。

借鉴 aiduMEI v18.3.0 MAP 面板数据服务（/knowledge/tree）的只读窄版：
兰台用自家 MemoryEdge + scene 归属，不引入作者的四类节点概念。
"""
from fastapi import APIRouter, HTTPException

from lantai.ops.graph import get_graph

router = APIRouter()


@router.get("/graph")
def graph_route(limit: int = 150):
    """记忆关系星图（只读）：节点 + MemoryEdge 链接 + lane/relation 统计。"""
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise HTTPException(422, "limit must be an int in [1, 500]")
    return get_graph(limit)
