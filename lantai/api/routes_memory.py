from fastapi import APIRouter, Depends, HTTPException

from lantai.core.acl import lane_allowed, verify_agent
from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.services.memory_service import (
    add_memory,
    add_memory_async,
    add_raw_memory,
    get_core_memory,
    list_memories,
    put_core_memory,
)

router = APIRouter()


def _guard_lane_allowed(agent_id: str, lane: str | None) -> None:
    """写入侧 ACL 守卫：未绑定 lane → 403（宁 miss 不脏写）。"""
    if not lane_allowed(agent_id, lane or "general"):
        raise HTTPException(403, f"lane '{lane or 'general'}' not bound for agent")


@router.post("/add")
def add_memory_route(req: AddMemoryReq, async_mode: bool = False,
                     agent_id: str = Depends(verify_agent)):
    _guard_lane_allowed(agent_id, req.lane)
    if async_mode:
        return add_memory_async(req, user_id=agent_id)
    return add_memory(req, user_id=agent_id)


@router.get("/core-memory")
def get_core_memory_route(namespace: str = "default"):
    return get_core_memory(namespace)


@router.put("/core-memory")
def put_core_memory_route(block: str, content: str, namespace: str = "default"):
    try:
        return put_core_memory(block, content, namespace)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/add/raw")
def add_raw_memory_route(req: RawMemoryReq, agent_id: str = Depends(verify_agent)):
    """原文直存（verbatim）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""
    _guard_lane_allowed(agent_id, req.lane)
    return add_raw_memory(req)

@router.get("/memories")
def list_memories_route(
    lane: str = "",
    status: str = "",
    decay_class: str = "",
    memory_type: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """档案浏览（VAULT）：只读分页 + 过滤，受保护。"""
    try:
        return list_memories(
            lane=lane, status=status, decay_class=decay_class,
            memory_type=memory_type, limit=limit, offset=offset,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

