"""Obsidian 双链同步 + verbatim 专用检索（Ticket 02，借鉴 aiduMEI v18.3）。

原文直存默认不进混合召回（VERBATIM_IN_RECALL=false），GET /verbatim/search
为专用通道；POST /obsidian/sync 幂等（content_hash + 实体名去重）。
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
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
def verbatim_search_route(q: str, top_k: int = 5, ctx=Depends(get_current_user)):
    """verbatim 专用检索：原文直存默认不进混合召回，此通道可查（FTS+向量）。

    归属收窄（票 .scratch/readside-gaps/07）：此前不传 `principal`，
    而底层 `hybrid_search` / `search_fts` / `search_fts_bm25` / LIKE 兜底
    **本来就支持**归属过滤（`principal` 非 None 时加 `AND m.user_id = ?`），
    于是 A 一次查询就拿到整条 B 的 `MemoryItem`——`content` 全文 + ULID
    `id` + `user_id`。同文件的 `routes_search.py`（主 `/search`）与
    `routes_terminal.py` 都传了，只有这一条漏了；补一个参数即可，
    **不动检索内核**。

    `principal=None`（内部调用 / `cli/mcp.py` 的 verbatim 工具）不加过滤，
    与改动前逐字一致。
    """
    return hybrid_search(q, top_k=top_k, memory_types=["verbatim"], use_rerank=False, principal=ctx)
