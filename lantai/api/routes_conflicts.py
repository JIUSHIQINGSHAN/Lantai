"""冲突账本路由（P0-2）——薄路由，业务全在 conflict_service。

GET  /conflicts              冲突事件列表（默认 open）
POST /conflicts/{id}/resolve  人工裁决：resolved / dismissed
"""
from fastapi import APIRouter, HTTPException

from lantai.services import conflict_service

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(limit: int = 50, status: str = "open"):
    try:
        return conflict_service.list_conflict_events(limit, status)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/conflicts/{event_id}/resolve")
def resolve_conflict(event_id: str, decision: str, note: str = ""):
    try:
        return conflict_service.resolve_conflict_event(event_id, decision, note)
    except ValueError as e:
        raise HTTPException(400, str(e))
