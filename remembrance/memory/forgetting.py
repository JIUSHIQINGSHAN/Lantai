import math
from datetime import timedelta
from sqlmodel import select
from remembrance.core.time import utcnow
from remembrance.core.settings import settings
from remembrance.models.tables import MemoryItem
from remembrance.storage import db


def _lane_strength(importance: float, use_count: int, lane: str) -> float:
    """按 lane profile 计算记忆保持强度 S"""
    profile = settings.LANE_DECAY_PROFILES.get(lane, settings.LANE_DECAY_PROFILES["general"])
    base_s = profile["base_s"]
    boost = profile["importance_boost"]
    return base_s + boost * importance + 2 * math.log1p(use_count)


def apply_forgetting():
    """衰减 + 自动归档。

    - 计算每条记忆的 decay_score（指数衰减）
    - decay 低于 ARCHIVE_DECAY_THRESHOLD 时自动转 archived
    - working memory 超过 TTL 且无帮助时转 archived
    - archived 记忆不参与检索（WHERE status='active'），但物理不删
    """
    now = utcnow()
    with db.get_session() as s:
        for m in s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all():
            last = m.last_used_at or m.created_at
            days = max(0.0, (now - last).total_seconds() / 86400.0)
            strength = _lane_strength(m.importance, m.use_count, m.lane)
            m.decay_score = math.exp(-days / strength)

            # 自动归档：decay 极低 或 working memory 过期且无用
            if m.decay_score < settings.ARCHIVE_DECAY_THRESHOLD:
                m.status = "archived"
            elif (m.tier == "working"
                  and days > settings.WORKING_MEMORY_TTL_DAYS
                  and m.helpful_count == 0):
                m.status = "archived"

            s.add(m)
        s.commit()
