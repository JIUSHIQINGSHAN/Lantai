"""记忆广播链路由（v0.11 烽燧，借鉴 aiduMEI /recall_chain 窄版，只读）。"""
from fastapi import APIRouter, HTTPException

from lantai.ops.recall_chain import build_recall_chain, validate_chain_params

router = APIRouter(prefix="/recall", tags=["recall"])


@router.get("/chain")
def recall_chain_route(q: str = "", max_depth: int = 3, branch: int = 3,
                       min_score: float = 0.3, total_max: int = 20) -> dict:
    """记忆广播链（只读）：seed 记忆逐层触发关联记忆（烽燧相传）。"""
    try:
        validate_chain_params(max_depth, branch, min_score, total_max)
        return build_recall_chain(q, max_depth, branch, min_score, total_max)
    except ValueError as e:
        raise HTTPException(422, str(e))
