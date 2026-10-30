"""钩沉（ADR-0082 · 钩深致远）：历史语境回溯与沉埋记忆检索恢复服务层。

基于认知层 GouchenEngine 统一封装：
1. gouchen_dredge_service: 沉埋记忆穿透打捞（支持关键字与沉没度过滤）；
2. gouchen_snapshot_service: 基于时间锚点重建历史语境快照（无未来污染）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from lantai.cognition.gouchen import GouchenEngine, GouchenResult, HistoricalContext


def gouchen_dredge_service(
    query: str = "",
    min_sunken_score: float = 0.4,
    limit: int = 50,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """穿透打捞沉埋、归档或弱化的历史记忆。"""
    engine = GouchenEngine(session=session, principal=principal)
    res: GouchenResult = engine.dredge_sunken_memories(
        query=query,
        min_sunken_score=min_sunken_score,
        limit=limit,
        session=session,
    )
    return res.to_dict(fenced=True)


def gouchen_snapshot_service(
    anchor_time: datetime,
    limit: int = 50,
    session: Session | None = None,
    principal=None,
) -> dict[str, Any]:
    """基于时序锚点重建历史语境快照（杜绝未来信息污染）。"""
    engine = GouchenEngine(session=session, principal=principal)
    ctx: HistoricalContext = engine.reconstruct_historical_context(
        anchor_time=anchor_time,
        limit=limit,
        session=session,
    )
    return ctx.to_dict(fenced=True)
