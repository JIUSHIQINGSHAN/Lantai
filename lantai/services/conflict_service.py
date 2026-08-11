"""冲突账本 service 层（P0-2）：list / resolve ConflictEvent。

账本由 gate/decision.py 在闸门决策时写入（确定性规则命中）；此处提供审计
与人工裁决入口（REST + MCP），裁决不影响闸门结果，只标记处置。
"""
from datetime import datetime

from sqlmodel import select

from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent
from lantai.storage import db

_ALLOWED_STATUSES = ("open", "resolved", "dismissed")


def list_conflict_events(limit: int = 50, status: str = "open") -> dict:
    """冲突账本列表（默认 open），按创建时间倒序。"""
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {_ALLOWED_STATUSES}")
    with db.get_session() as s:
        q = select(ConflictEvent).order_by(ConflictEvent.created_at.desc()).limit(limit)
        if status != "all":
            q = q.where(ConflictEvent.status == status)
        rows = s.exec(q).all()
        return {"events": [r.model_dump(mode="json") for r in rows]}


def resolve_conflict_event(event_id: str, decision: str, note: str = "") -> dict:
    """人工裁决冲突事件：decision ∈ resolved（确认冲突成立）/ dismissed（误报）。"""
    if decision not in ("resolved", "dismissed"):
        raise ValueError("decision must be 'resolved' or 'dismissed'")
    with db.get_session() as s:
        ev = s.get(ConflictEvent, event_id)
        if not ev:
            raise ValueError("conflict event not found")
        if ev.status != "open":
            raise ValueError(f"conflict event not open (status={ev.status})")
        ev.status = decision
        ev.resolved_by = note[:200] or "manual"
        ev.resolved_at = utcnow()
        s.add(ev)
        s.commit()
        return {"ok": True, "event_id": ev.id, "status": ev.status}
