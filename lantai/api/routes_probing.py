"""探颐（ADR-0037）：主动探针 REST 路由。"""

from fastapi import APIRouter
from pydantic import BaseModel

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
def probing_detect_route(req: ProbeDetectReq):
    """探颐：检测当前查询相关的未决冲突主动求证探针。"""
    probes = detect_memory_probes(query=req.query, session_id=req.session_id)
    return {
        "probes": probes,
        "prompt_context": format_probing_context(probes),
    }


@router.post("/probing/resolve")
def probing_resolve_route(req: ProbeResolveReq):
    """探颐：根据用户自然语言答复自动闭环消解冲突。"""
    return resolve_probe_response(
        conflict_id=req.conflict_id,
        user_reply=req.user_reply,
    )
