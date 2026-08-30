"""札记路由（ADR-0032）：Agent 主动工作区暂存夹 REST 端点。

GET  /scratchpad/{session_id}  读取札记
POST /scratchpad/{session_id}  更新札记
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lantai.services.scratchpad_service import get_scratchpad, write_scratchpad

router = APIRouter(tags=["scratchpad"])


class WriteScratchpadReq(BaseModel):
    content: str = ""


@router.get("/scratchpad/{session_id}")
def scratchpad_get_route(session_id: str):
    """读取指定会话的札记便签。"""
    content = get_scratchpad(session_id)
    return {"session_id": session_id, "content": content}


@router.post("/scratchpad/{session_id}")
def scratchpad_write_route(session_id: str, req: WriteScratchpadReq):
    """更新/覆盖指定会话的札记便签。"""
    try:
        return write_scratchpad(session_id, req.content)
    except Exception as e:
        raise HTTPException(422, str(e)) from e
