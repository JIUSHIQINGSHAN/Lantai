from fastapi import APIRouter, HTTPException

from lantai.models.schemas import AddMemoryReq, RawMemoryReq
from lantai.services.memory_service import (
    add_memory, add_memory_async, get_core_memory, put_core_memory, add_raw_memory,
    list_memories,
)

router = APIRouter()


@router.post("/add")
def add_memory_route(req: AddMemoryReq, async_mode: bool = False):
    if async_mode:
        return add_memory_async(req)
    return add_memory(req)


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
def add_raw_memory_route(req: RawMemoryReq):
    """原文直存（verbatim）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""
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

