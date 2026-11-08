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

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.acl import SESSION_ID_PATTERN
from lantai.core.auth import get_current_user
from lantai.services.scratchpad_service import get_scratchpad, write_scratchpad

router = APIRouter(tags=["scratchpad"])


class WriteScratchpadReq(BaseModel):
    # 票 54：字符集收窄（纵深第二层）——session_id 进提炼链围栏 item_id
    # 属性（zhaji-{sid}），pydantic pattern 非法即 422，不静默截断；
    # 路径形态（POST /scratchpad/{session_id}）无 body 模型闸，路由内再钳。
    session_id: str = Field(default="default", pattern=SESSION_ID_PATTERN)
    content: str = ""


class DistillScratchpadReq(BaseModel):
    session_id: str = Field(default="default", pattern=SESSION_ID_PATTERN)
    content: str = ""
    write: bool = False
    # 票 42：提纯输出长度是 LLM 提示面（进 checkpoint → shell hook → 插件），
    # 上限对齐 MAX_SCRATCHPAD_CHARS 的 10 倍封顶；100 以下无提纯意义
    max_chars: int = Field(default=1000, ge=100, le=10000)


@router.post("/scratchpad/distill")
def scratchpad_distill_route(
    req: DistillScratchpadReq,
    ctx=Depends(get_current_user),
):
    """札记结构化提纯与认知压缩端点（ADR-0080）。"""
    from lantai.services.scratchpad_service import zhaji_distill_service

    try:
        return zhaji_distill_service(
            session_id=req.session_id,
            raw_text=req.content,
            write=req.write,
            max_chars=req.max_chars,
            principal=ctx,
        )
    except PermissionError as pe:
        raise HTTPException(403, str(pe)) from pe
    except HTTPException:
        raise
    except ValueError as ve:
        # 票 41：模板统一到 kaogong 版——ValueError（可校验输入）→422，
        # 其余异常放给 500 全局 handler（服务器故障不得计入调用方滥用，
        # str(e) 不得泄内部文本进响应体）
        raise HTTPException(422, str(ve)) from ve


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
    # 票 54：路径形态的 session_id 不经 body 模型——解析后统一过字符集
    # （非法 422，不静默截断；body 形态另有 Field pattern 双保险）。
    if not re.fullmatch(SESSION_ID_PATTERN, sid):
        raise HTTPException(422, "session_id must match ^[A-Za-z0-9_\\-.]{3,128}$")
    try:
        return write_scratchpad(sid, req.content, principal=ctx)
    except HTTPException:
        raise
    except ValueError as ve:
        # 票 41：模板统一到 kaogong 版——ValueError（可校验输入）→422，
        # 其余异常放给 500 全局 handler（服务器故障不得计入调用方滥用，
        # str(e) 不得泄内部文本进响应体）
        raise HTTPException(422, str(ve)) from ve
