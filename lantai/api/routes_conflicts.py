"""冲突账本路由（P0-2）——薄路由，业务全在 conflict_service。

GET  /conflicts              冲突事件列表（默认 open）
POST /conflicts/{id}/resolve  人工裁决：resolved / dismissed

归属（票 .scratch/readside-gaps/07 修法口径 3）：此前两条都不取身份——
读侧把别人的 `incoming_ref` 正文连同 `memory_id` 全表吐出；
写侧实测 A 能把 B 的冲突 `open → dismissed`（替 B 做判断）。
校验在 service 层（worker/MCP 也走那条路），这里只负责把 principal 传下去。
"""

from fastapi import APIRouter, Depends, HTTPException

from lantai.core.auth import get_current_user
from lantai.models.schemas import ConflictResolveReq
from lantai.services import conflict_service

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(limit: int = 50, status: str = "open", ctx=Depends(get_current_user)):
    try:
        return conflict_service.list_conflict_events(limit, status, principal=ctx)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/conflicts/{event_id}/resolve")
def resolve_conflict(event_id: str, req: ConflictResolveReq, ctx=Depends(get_current_user)):
    """人工裁决：resolved / dismissed；decision 与 note 走 JSON body（曾走 query，长文本会 414 且进访问日志）。"""
    try:
        return conflict_service.resolve_conflict_event(
            event_id, req.decision, req.note, principal=ctx
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
