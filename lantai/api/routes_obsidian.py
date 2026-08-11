"""Obsidian 双链同步 + verbatim 专用检索（Ticket 02，借鉴 aiduMEI v18.3）。

原文直存默认不进混合召回（VERBATIM_IN_RECALL=false），GET /verbatim/search
为专用通道；POST /obsidian/sync 幂等（content_hash + 实体名去重）。
"""
from fastapi import APIRouter, HTTPException

from lantai.models.schemas import ObsidianSyncReq
from lantai.retrieval.hybrid import hybrid_search
from lantai.services.obsidian_service import sync_obsidian_note

router = APIRouter()


@router.post("/obsidian/sync")
def obsidian_sync_route(req: ObsidianSyncReq):
    """笔记原文直存 + [[双链]] 实体/边沉淀（content_hash + 实体名幂等）。"""
    try:
        return sync_obsidian_note(req)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/verbatim/search")
def verbatim_search_route(q: str, top_k: int = 5):
    """verbatim 专用检索：原文直存默认不进混合召回，此通道可查（FTS+向量）。"""
    return hybrid_search(q, top_k=top_k, memory_types=["verbatim"], use_rerank=False)
