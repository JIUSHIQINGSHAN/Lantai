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
def obsidian_sync_route(req: ObsidianSyncReq, ctx=Depends(get_current_user)):
    """笔记原文直存 + [[双链]] 实体/边沉淀（content_hash + 实体名幂等）。

    归属（票 `.scratch/readside-gaps/20`）：此前**一个身份都不取**——`EXT_ROUTER`
    带 `dependencies=AUTH` 所以认证了某人，却把身份丢掉了。同文件的
    `/verbatim/search` 本来就 `Depends(get_current_user)`，只有这一条漏了
    （形状同票 19：**同一个文件里一条修了一条没修**）。

    缺了身份，A 提交一段与 B 的 verbatim 内容 sha256 相同的文本，`add_raw_memory`
    的去重无归属过滤 → 返回 **B 的** `memory_id` → 本路由把它当 `note_id`
    交回前端，A 拿到别人的 ULID；`sync_obsidian_note` 还会拿这个 id 取到 B 的行，
    于是 **A 的双链实体从 B 的记忆行连出去**，污染 B 的图邻域。

    lane 校验**必须在路由层先做**（同 `routes_memory.add_raw_memory_route` 的
    第一行）：service 里那道 `ensure_can_delete` 是在 `add_raw_memory`
    **写完之后**才判 lane 的（`.scratch/readside-gaps/probe_m5_lane.py` 实测：
    越 lane 请求返回 403，可 verbatim 行**已经落库**）。先写后拒的语义是
    「调用方以为失败了，数据却留下了」——所以这里在进 service 之前就挡掉。

    `allowed_lanes is None` 是「未绑定、不限泳道」（`acl.ensure_can_delete`
    同口径），不是「零泳道」——所以判空前先判 None。
    """
    if ctx.allowed_lanes is not None and req.lane not in ctx.allowed_lanes:
        raise HTTPException(status_code=403, detail=f"Lane {req.lane} not allowed for agent")
    result = sync_obsidian_note(req, principal=ctx)
    if isinstance(result, dict) and result.get("ok") is False:
        reason = result.get("reason", "")
        status = 403 if str(reason).startswith("forbidden") else 400
        raise HTTPException(status, reason)
    return result


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
