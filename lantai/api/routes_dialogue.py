"""对话写通道路由（Ticket 01）——薄路由，业务全在 dialogue.ingest_dialogue。

POST /dialogue   对话文本写入（fastpath 直通 / 提取建候选 / 闲聊入待审队列）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lantai.ingestion.dialogue import ingest_dialogue

router = APIRouter(tags=["dialogue"])


class DialogueIngestReq(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    user_id: str = "default"
    source: str = "dialogue"


@router.post("/dialogue")
def dialogue_route(req: DialogueIngestReq):
    try:
        return ingest_dialogue(req.text, user_id=req.user_id, source=req.source)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/dialogue/async")
def dialogue_async_route(req: DialogueIngestReq):
    """潜移（ADR-0033）：异步提交对话进行提纯摄取，立即返回 task_id。"""
    try:
        from lantai.services.async_ingest_service import submit_async_dialogue
        return submit_async_dialogue(req.text, user_id=req.user_id, source=req.source)
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

