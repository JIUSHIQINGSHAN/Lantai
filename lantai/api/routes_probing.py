"""探颐（ADR-0037）：主动探针 REST 路由。

归属（票 .scratch/readside-gaps/07 修法口径 4）：
- `POST /probing/resolve` 写的是**正文**（`item.content = conf.incoming_ref`），
  此前一个身份都不取，A 能覆盖 B 的记忆原文。按破坏性写操作处理，
  service 层复用 `acl.ensure_can_delete` 单一真源。
- `POST /probing/detect` 此前全表捞未决冲突，把别人的
  `existing_content` + `incoming_ref` 两份全文渲染成问句吐出来。

校验在 service 层（worker/MCP 也走那条路），这里只负责把 principal 传下去。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lantai.core.auth import get_current_user
from lantai.services.probing_service import (
    detect_memory_probes,
    format_probing_context,
    resolve_probe_response,
)

router = APIRouter()


class ProbeDetectReq(BaseModel):
    query: str
    session_id: str | None = None


class ProbeResolveReq(BaseModel):
    conflict_id: str
    user_reply: str


@router.post("/probing/detect")
def probing_detect_route(req: ProbeDetectReq, ctx=Depends(get_current_user)):
    """探颐：检测当前查询相关的未决冲突主动求证探针（按归属收窄）。"""
    probes = detect_memory_probes(query=req.query, session_id=req.session_id, principal=ctx)
    return {
        "probes": probes,
        "prompt_context": format_probing_context(probes),
    }


@router.post("/probing/resolve")
def probing_resolve_route(req: ProbeResolveReq, ctx=Depends(get_current_user)):
    """探颐：根据用户自然语言答复自动闭环消解冲突（含归属校验，403 不落库）。"""
    return resolve_probe_response(
        conflict_id=req.conflict_id,
        user_reply=req.user_reply,
        principal=ctx,
    )
