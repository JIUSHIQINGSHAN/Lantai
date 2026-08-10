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
