"""札记路由（ADR-0032）：Agent 主动工作区暂存夹 REST 端点。

GET  /scratchpad/{session_id}  读取札记
POST /scratchpad/{session_id}  更新札记

**归属（票 .scratch/ownership-gaps/04）**：两个端点都必须带
`get_current_user` 并把 principal 传给 service——札记只按 session_id
主键查，不带身份时 A 写的札记 B 一读就到，而札记会进 LLM 提示
（checkpoint_service → shell_hook → hermes 插件），等于别人的私有
文字进我的上下文。票 01 的教训是「加了 Depends 不 forwarding 等于没加」，
所以这里必须一路传到底。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.services.scratchpad_service import get_scratchpad, write_scratchpad

router = APIRouter(tags=["scratchpad"])


class WriteScratchpadReq(BaseModel):
    session_id: str = "default"
    content: str = ""


@router.get("/scratchpad")
@router.get("/scratchpad/{session_id}")
def scratchpad_get_route(session_id: str = "default", ctx=Depends(get_current_user)):
    """读取指定会话的札记便签（按归属收窄，票 ownership-gaps/04）。"""
    content = get_scratchpad(session_id, principal=ctx)
    return {"session_id": session_id, "content": content}


@router.post("/scratchpad")
@router.post("/scratchpad/{session_id}")
def scratchpad_write_route(
    req: WriteScratchpadReq,
    session_id: str = None,
    ctx=Depends(get_current_user),
):
    """更新/覆盖指定会话的札记便签（归属随 principal 落列，票 ownership-gaps/04）。"""
    sid = session_id or req.session_id or "default"
    try:
        return write_scratchpad(sid, req.content, principal=ctx)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, str(e)) from e
