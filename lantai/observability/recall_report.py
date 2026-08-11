"""零召回率监控 + token 成本估算（借鉴 TencentDB Agent Memory 可观测性）。

RetrievalEvent 只追加事件 → recall_report 按窗口聚合：
- 零召回率（排除系统注入噪音，is_system_noise）
- 按 lane / intent 分组定位检索缺口
- 场景维度（SCENE_LAYER_ENABLED 时命中场景成员的比例，scene_ids）
- token 成本粗估（查询 + 注入结果，零依赖）
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import RetrievalEvent
from lantai.storage import db


def estimate_tokens(text: str) -> int:
    """零依赖 token 粗估：CJK 字符按 1 token/字，其余按 4 字符/词元。

    纯函数、可单测。用于成本观测而非计费，粗估误差可接受。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4


def _scenes_from_results(results: list[dict]) -> list[str]:
    """命中记忆的 scene_id 去重排序（memory dict 可能来自 model_dump）。"""
    scenes = set()
    for r in results:
        mem = r.get("memory") if isinstance(r, dict) else None
        if isinstance(mem, dict) and mem.get("scene_id"):
            scenes.add(mem["scene_id"])
    return sorted(scenes)


def _tokens_from_results(results: list[dict]) -> int:
    total = 0
    for r in results:
        mem = r.get("memory") if isinstance(r, dict) else None
        if isinstance(mem, dict):
            total += estimate_tokens(mem.get("content") or "")
    return total


def recall_report(days: int | None = None) -> dict:
    """最近 N 天零召回率监控报告（窗口默认 RECALL_MONITOR_WINDOW_DAYS）。"""
    window = days if days is not None else settings.RECALL_MONITOR_WINDOW_DAYS
    if not isinstance(window, int) or isinstance(window, bool) or not (1 <= window <= 365):
        raise ValueError("days must be an int in [1, 365]")
    start = utcnow() - timedelta(days=window)
    with db.get_session() as s:
        events = s.exec(
            select(RetrievalEvent).where(RetrievalEvent.created_at >= start)).all()
    total = len(events)
    noise = sum(1 for e in events if e.is_system_noise)
    real = [e for e in events if not e.is_system_noise]
    zero = sum(1 for e in real if e.zero_result)
    by_lane: dict[str, dict] = {}
    by_intent: dict[str, dict] = {}
    scene_hits = 0
    scene_events = 0
    token_total = 0
    for e in real:
        lane = e.lane or "unknown"
        lane_stat = by_lane.setdefault(lane, {"total": 0, "zero": 0})
        lane_stat["total"] += 1
        if e.zero_result:
            lane_stat["zero"] += 1
        intent = e.intent_bucket or "unknown"
        intent_stat = by_intent.setdefault(intent, {"total": 0, "zero": 0})
        intent_stat["total"] += 1
        if e.zero_result:
            intent_stat["zero"] += 1
        token_total += int(e.estimated_tokens or 0)
        if settings.SCENE_LAYER_ENABLED:
            scene_ids = e.scene_ids or []
            scene_events += 1
            if scene_ids:
                scene_hits += 1
    real_n = len(real)
    return {
        "window_days": window,
        "start": start.isoformat(),
        "total": total,
        "system_noise": noise,
        "real": real_n,
        "zero": zero,
        "zero_recall_rate": round(zero / real_n, 4) if real_n else 0.0,
        "by_lane": by_lane,
        "by_intent": by_intent,
        "scene": {
            "enabled": bool(settings.SCENE_LAYER_ENABLED),
            "events": scene_events,
            "hit": scene_hits,
            "hit_rate": round(scene_hits / scene_events, 4) if scene_events else None,
        },
        "estimated_tokens": {
            "total": token_total,
            "avg_per_query": round(token_total / real_n, 1) if real_n else 0.0,
        },
    }


def recent_retrieval_events(limit: int = 20) -> list[dict]:
    """最近 N 条检索事件（新→旧），供 EVOLVE 看板事件流。

    与 recall_report 共用 RetrievalEvent；只读聚合，含噪音标记由前端展示。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 100):
        raise ValueError("limit must be an int in [1, 100]")
    with db.get_session() as s:
        events = s.exec(
            select(RetrievalEvent).order_by(RetrievalEvent.created_at.desc())
            .limit(limit)).all()
    return [{
        "id": e.id,
        "query": (e.query_text or "")[:120],
        "lane": e.lane or "unknown",
        "intent": e.intent_bucket or "unknown",
        "latency_ms": e.latency_ms,
        "zero_result": e.zero_result,
        "is_system_noise": e.is_system_noise,
        "estimated_tokens": e.estimated_tokens,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events]
