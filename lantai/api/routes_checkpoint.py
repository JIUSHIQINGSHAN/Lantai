"""检查点 API——薄 handler，逻辑下沉 service 层（F13 补齐 + ADR-0021 底本）

- GET  /checkpoint?memory_id=…        记忆变更快照列表（回滚用）
- POST /checkpoint                    底本：写入五段会话快照（session_id + blocks）
- GET  /checkpoint/latest             底本：最近一次会话快照
- GET  /checkpoint?session_id=…       底本：指定会话快照
- POST /checkpoint/cleanup            底本：只保留最近 N 个会话快照
"""
from fastapi import APIRouter
from pydantic import BaseModel

from lantai.services.evolution_service import list_checkpoints
from lantai.services.checkpoint_service import (
    write_session_checkpoint, get_checkpoint, get_latest_checkpoint,
    cleanup_old_checkpoints,
)

router = APIRouter()


class CheckpointWriteReq(BaseModel):
    session_id: str
    blocks: dict


@router.get("/checkpoint")
def list_checkpoints_route(memory_id: str = "", limit: int = 20,
                           session_id: str = ""):
    if session_id:
        return get_checkpoint(session_id)
    return list_checkpoints(memory_id, limit)


@router.post("/checkpoint")
def write_checkpoint_route(req: CheckpointWriteReq):
    return write_session_checkpoint(req.session_id, req.blocks)


@router.get("/checkpoint/latest")
def latest_checkpoint_route():
    return get_latest_checkpoint()


@router.post("/checkpoint/cleanup")
def cleanup_checkpoint_route(max_sessions: int | None = None):
    return cleanup_old_checkpoints(max_sessions)
