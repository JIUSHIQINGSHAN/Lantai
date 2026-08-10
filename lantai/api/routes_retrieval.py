"""检索观测路由：used_ids 弱标注回填（方向二）。

生成侧（Hermes）在回答后调用 POST /retrieval/backfill，
把实际用进回答的记忆 id 写回检索事件，供 dry-run 算 weak_hit_rate。
失败零侵入（返回 404/400，不抛 500 阻断主链路）。
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from lantai.observability.retrieval_log import backfill_used_ids

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class BackfillReq(BaseModel):
    event_id: str = Field(min_length=1)
    used_ids: list[str] = Field(default_factory=list)


@router.post("/backfill")
def backfill(req: BackfillReq) -> dict:
    """回填 used_ids：把生成侧实际用到的记忆 id 关联到检索事件。"""
    backfill_used_ids(req.event_id, req.used_ids)
    return {"ok": True, "event_id": req.event_id, "used_count": len(req.used_ids)}
