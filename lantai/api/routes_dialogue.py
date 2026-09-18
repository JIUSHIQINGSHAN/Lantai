"""对话写通道路由（Ticket 01）——薄路由，业务全在 dialogue.ingest_dialogue。

POST /dialogue   对话文本写入（fastpath 直通 / 提取建候选 / 闲聊入待审队列）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.auth import Principal, get_current_user
from lantai.ingestion.dialogue import ingest_dialogue

router = APIRouter(tags=["dialogue"])


class DialogueIngestReq(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    user_id: str = "default"
    source: str = "dialogue"
    # 来源链显式透传（v022 票据 01）：出身由写入方声明，不靠隐式通道
    session_id: str = Field(default="", max_length=128)
    turn: int | None = Field(default=None, ge=0)


@router.post("/dialogue")
def dialogue_route(req: DialogueIngestReq, ctx: Principal = Depends(get_current_user)):
    req.user_id = ctx.user_id or req.user_id
    try:
        return ingest_dialogue(
            req.text,
            user_id=req.user_id,
            source=req.source,
            session_id=req.session_id,
            turn=req.turn,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/dialogue/async")
def dialogue_async_route(req: DialogueIngestReq, ctx: Principal = Depends(get_current_user)):
    req.user_id = ctx.user_id or req.user_id
    """潜移（ADR-0033）：异步提交对话进行提纯摄取，立即返回 task_id。"""
    try:
        from lantai.services.async_ingest_service import submit_async_dialogue

        return submit_async_dialogue(
            req.text,
            user_id=req.user_id,
            source=req.source,
            session_id=req.session_id,
            turn=req.turn,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/dialogue/tasks/{task_id}")
def dialogue_task_status_route(task_id: str):
    """潜移（ADR-0033）：查询异步对话摄取任务的状态与结果。"""
    from lantai.services.async_ingest_service import get_task_status

    res = get_task_status(task_id)
    if res.get("status") == "not_found":
        raise HTTPException(404, "任务未找到")
    return res


class SessionDistillReq(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    store: bool = False  # true = 提炼并经完整闸门管线落库；false = 只提炼不落库


@router.post("/session/distill")
def session_distill_route(req: SessionDistillReq, ctx: Principal = Depends(get_current_user)):
    """咀华（v022 票据 04，上游 session distill）：会话精华萃取。

    只提炼不落库可安全重跑（排查「这次怎么没精华」不用把已结束的会话
    再结束一遍）；store=true 时经 add_memory 走完整闸门管线进向量库，
    且要求调用者泳道集含 distill（写入路由同款检查，整改票 03）。"""
    if req.store and "distill" not in set(ctx.allowed_lanes or []):
        raise HTTPException(403, "distill 泳道未授权：当前密钥不能落库精华")
    try:
        from lantai.services.distill_service import distill_session

        return distill_session(req.session_id, store=req.store, principal=ctx)
    except ValueError as e:
        raise HTTPException(422, str(e))
