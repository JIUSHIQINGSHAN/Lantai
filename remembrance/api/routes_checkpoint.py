from fastapi import APIRouter
from sqlmodel import select
from remembrance.models.tables import MemoryCheckpoint
from remembrance.storage import db

router = APIRouter()

@router.get("/checkpoint")
def list_checkpoints(memory_id: str, limit: int = 20):
    with db.get_session() as s:
        rows = s.exec(select(MemoryCheckpoint)
                      .where(MemoryCheckpoint.memory_id == memory_id)
                      .order_by(MemoryCheckpoint.version.desc())
                      .limit(limit)).all()
        return {"checkpoints": [r.model_dump(mode="json") for r in rows]}
