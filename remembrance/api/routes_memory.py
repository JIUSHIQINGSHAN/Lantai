from fastapi import APIRouter, HTTPException

from remembrance.models.schemas import AddMemoryReq
from remembrance.services.memory_service import add_memory, get_core_memory, put_core_memory

router = APIRouter()


@router.post("/add")
def add_memory_route(req: AddMemoryReq):
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
