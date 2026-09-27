"""检查点 API——薄 handler，逻辑下沉 service 层（F13 补齐 + ADR-0021 底本）

- GET  /checkpoint?memory_id=…        记忆变更快照列表（回滚用）
- POST /checkpoint                    底本：写入五段会话快照（session_id + blocks）
- GET  /checkpoint/latest             底本：最近一次会话快照
- GET  /checkpoint?session_id=…       底本：指定会话快照
- POST /checkpoint/cleanup            底本：只保留最近 N 个会话快照

归属（票 .scratch/readside-gaps/09）：4 个 handler 原本一个身份都不取，
`SessionCheckpoint` 的归属四元组（`tables.py:658`）也一直在，只是写入方
从不填、读取方从不校验。A 能按 session_id 直读任意会话的五段底本
（在做/下一步/工作区/决策/待办）——那是把当前工作现场整个吐出去。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.services.checkpoint_service import (
    cleanup_old_checkpoints,
    get_checkpoint,
    get_latest_checkpoint,
    write_session_checkpoint,
)
from lantai.services.evolution_service import list_checkpoints

router = APIRouter()


class CheckpointWriteReq(BaseModel):
    session_id: str
    blocks: dict


@router.get("/checkpoint")
def list_checkpoints_route(
    memory_id: str = "",
    limit: int = 20,
    session_id: str = "",
    ctx=Depends(get_current_user),
):
    if session_id:
        return get_checkpoint(session_id, principal=ctx)
    return list_checkpoints(memory_id, limit)


@router.post("/checkpoint")
def write_checkpoint_route(req: CheckpointWriteReq, ctx=Depends(get_current_user)):
    return write_session_checkpoint(req.session_id, req.blocks, principal=ctx)


@router.get("/checkpoint/latest")
def latest_checkpoint_route(ctx=Depends(get_current_user)):
    return get_latest_checkpoint(principal=ctx)


@router.post("/checkpoint/cleanup")
def cleanup_checkpoint_route(max_sessions: int | None = None, ctx=Depends(get_current_user)):
    return cleanup_old_checkpoints(max_sessions, principal=ctx)
