"""用量聚合（v0.8，借鉴 aiduMEI mem_usage 收尾）。

REST /usage 与 MCP mem_usage 共用的纯聚合服务：最近 N 天每日新增记忆数，
单条 GROUP BY 不整表加载，缺日补零（与报告窗口一致）。
"""
from datetime import timedelta

from sqlmodel import func, select

from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage import db


def collect_usage(days: int = 7) -> dict:
    """最近 days 天每日新增记忆数（含今天，缺日补零）。"""
    if not isinstance(days, int) or isinstance(days, bool) or not (1 <= days <= 365):
        raise ValueError("days must be an int in [1, 365]")
    base = utcnow().date() - timedelta(days=days - 1)
    since = utcnow() - timedelta(days=days - 1)  # 与报告窗口一致（today-(days-1) .. today）
    with db.get_session() as s:
        rows = s.exec(
            select(func.date(MemoryItem.created_at), func.count())
            .where(MemoryItem.created_at >= since)
            .group_by(func.date(MemoryItem.created_at))
        ).all()
    daily = {str(d): c for d, c in rows}
    return {
        "daily_new": {
            str(base + timedelta(days=i)): daily.get(str(base + timedelta(days=i)), 0)
            for i in range(days)
        }
    }
