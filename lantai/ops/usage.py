"""用量聚合（v0.8，借鉴 aiduMEI mem_usage 收尾）。

REST /usage 与 MCP mem_usage 共用的纯聚合服务：最近 N 天每日新增记忆数，
单条 GROUP BY 不整表加载，缺日补零（与报告窗口一致）。
"""

from datetime import timedelta

from sqlmodel import func, select

from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage import db


def collect_usage(days: int = 7, principal=None) -> dict:
    """最近 days 天每日新增记忆数（含今天，缺日补零）。

    归属（票 `.scratch/mcp-identity-gaps/01b`）：此前每日计数全表不带
    身份，MCP 的 `mem_usage` 于是把**别人**每天写了多少记忆吐出来——
    那是行为画像素材（某天突然高产往往对应某件事）。按 viewer 收窄，
    口径同票 05 的 `_overview_scope`：`user_id == viewer OR IS NULL`
    （NULL 老行可见——单人部署下 629/650 行 NULL，判不可见等于报表归零）。
    `principal=None`（内部 worker / 脚本）与 admin 全量。
    """
    if not isinstance(days, int) or isinstance(days, bool) or not (1 <= days <= 365):
        raise ValueError("days must be an int in [1, 365]")
    base = utcnow().date() - timedelta(days=days - 1)
    since = utcnow() - timedelta(days=days - 1)  # 与报告窗口一致（today-(days-1) .. today）
    scope = None
    if principal is not None and not bool(getattr(principal, "is_admin", False)):
        from lantai.core.acl import viewer_of

        # 口径同 `_overview_scope` / `_digest_scope`（逐字）：`== viewer
        # OR IS NULL`。NULL 老行**可见**——真实库 650 行里 629 行 NULL，
        # 判不可见等于报表归零，那是修废不是收窄
        scope = (MemoryItem.user_id == viewer_of(principal)) | MemoryItem.user_id.is_(None)
    with db.get_session() as s:
        q = (
            select(func.date(MemoryItem.created_at), func.count())
            .where(MemoryItem.created_at >= since)
            .group_by(func.date(MemoryItem.created_at))
        )
        if scope is not None:
            q = q.where(scope)
        rows = s.exec(q).all()
    daily = {str(d): c for d, c in rows}
    return {
        "daily_new": {
            str(base + timedelta(days=i)): daily.get(str(base + timedelta(days=i)), 0)
            for i in range(days)
        }
    }
