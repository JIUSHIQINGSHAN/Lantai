"""冲突账本路由（P0-2）——薄路由，业务全在 conflict_service。

GET  /conflicts              冲突事件列表（默认 open）
POST /conflicts/{id}/resolve  人工裁决：resolved / dismissed
"""

from fastapi import APIRouter, HTTPException

from lantai.models.schemas import ConflictResolveReq
from lantai.services import conflict_service

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(limit: int = 50, status: str = "open"):
    try:
        return conflict_service.list_conflict_events(limit, status)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/conflicts/{event_id}/resolve")
def resolve_conflict(event_id: str, req: ConflictResolveReq):
    """人工裁决：resolved / dismissed；decision 与 note 走 JSON body（曾走 query，长文本会 414 且进访问日志）。"""
    try:
        return conflict_service.resolve_conflict_event(event_id, req.decision, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
