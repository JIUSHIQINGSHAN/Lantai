"""检索观测路由：used_ids 弱标注回填（方向二）。

生成侧（Hermes）在回答后调用 POST /retrieval/backfill，
把实际用进回答的记忆 id 写回检索事件，供 dry-run 算 weak_hit_rate。
失败零侵入（返回 404/400，不抛 500 阻断主链路）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lantai.core.auth import get_current_user
from lantai.observability.recall_report import recall_report, recent_retrieval_events
from lantai.observability.retrieval_log import backfill_used_ids

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


class BackfillReq(BaseModel):
    event_id: str = Field(min_length=1)
    used_ids: list[str] = Field(default_factory=list)


@router.post("/backfill")
def backfill(req: BackfillReq, ctx=Depends(get_current_user)) -> dict:
    """回填 used_ids：把生成侧实际用到的记忆 id 关联到检索事件。

    归属（票 `.scratch/mcp-identity-gaps/01c`）：此前**一个身份都不取**
    ——A 拿 B 的 `event_id` 就能把 B 的回执从 `pending` 改成 `acked`
    并覆盖成 A 给的值。这不是读泄漏，是**脏写**：回执链是 ADR-0049
    弱标注的地基。`get_current_user` 本就在本模块 import 着（:11），
    只是这条路由没用它——同票 24「签名有、函数体不用」的形状。
    """
    backfill_used_ids(req.event_id, req.used_ids, principal=ctx)
    return {"ok": True, "event_id": req.event_id, "used_count": len(req.used_ids)}


@router.get("/recall-report")
def get_recall_report(days: int | None = None) -> dict:
    """零召回率监控报告：最近 N 天检索事件聚合（排除系统噪音）。"""
    try:
        return recall_report(days)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/recent-events")
def get_recent_events(limit: int = 20, ctx=Depends(get_current_user)) -> dict:
    """最近检索事件流（新→旧，EVOLVE 看板用）。

    归属（票 .scratch/readside-gaps/10）：`query` 是用户问过什么，
    比记忆正文更直接暴露意图，此前一个身份都不取、全表倒序吐。
    """
    try:
        return {"events": recent_retrieval_events(limit, principal=ctx)}
    except ValueError as e:
        raise HTTPException(400, str(e))
