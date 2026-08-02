"""检查点 API——薄 handler，逻辑下沉 evolution_service（F13 补齐）"""
from fastapi import APIRouter

from remembrance.services.evolution_service import list_checkpoints

router = APIRouter()


@router.get("/checkpoint")
def list_checkpoints_route(memory_id: str, limit: int = 20):
    return list_checkpoints(memory_id, limit)
