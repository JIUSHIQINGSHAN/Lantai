import math
from datetime import timedelta, timezone
from sqlmodel import select
from lantai.core.time import utcnow
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem
from lantai.storage import db


def _lane_strength(importance: float, use_count: int, lane: str) -> float:
    """按 lane profile 计算记忆保持强度 S"""
    profile = settings.LANE_DECAY_PROFILES.get(lane, settings.LANE_DECAY_PROFILES["general"])
    base_s = profile["base_s"]
    boost = profile["importance_boost"]
    return base_s + boost * importance + 2 * math.log1p(use_count)


def apply_forgetting():
    """衰减 + 自动归档。

    - 计算每条记忆的 decay_score（指数衰减）
    - 跳过 |Δdecay| < 0.001 的更新，减少数据库无谓写入 (Ticket 2.4 [DD-06])
    - 分批 commit 减少 WAL 锁竞争 (Ticket 2.4 [DD-06])
    - decay 低于 ARCHIVE_DECAY_THRESHOLD 时自动转 archived
    - working memory 超过 TTL 且无帮助时转 archived
    - archived 记忆不参与检索（WHERE status='active'），但物理不删
    """
    now = utcnow()
    batch_size = 100
    with db.get_session() as s:
        batch_count = 0
        for m in s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all():
            changed = False
            # procedural 永不衰减：跳过衰减与归档判定，铁律天然浮顶
            if m.decay_class == "procedural":
                if m.decay_score != 1.0:
                    m.decay_score = 1.0
                    changed = True
            else:
                last = m.last_used_at or m.created_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                days = max(0.0, (now - last).total_seconds() / 86400.0)
                strength = _lane_strength(m.importance, m.use_count, m.lane)
                new_decay = math.exp(-days / strength)
                
                # Ticket 2.4 [DD-06]: 跳过极微小更新
                if abs(m.decay_score - new_decay) >= 0.001:
                    m.decay_score = new_decay
                    changed = True

                # 自动归档：decay 极低 或 working memory 过期且无用
                if m.decay_score < settings.ARCHIVE_DECAY_THRESHOLD and m.status != "archived":
                    m.status = "archived"
                    changed = True
                elif (m.tier == "working"
                      and days > settings.WORKING_MEMORY_TTL_DAYS
                      and m.helpful_count == 0
                      and m.status != "archived"):
                    m.status = "archived"
                    changed = True

            if changed:
                s.add(m)
                batch_count += 1
                if batch_count >= batch_size:
                    s.commit()
                    batch_count = 0

        if batch_count > 0:
            s.commit()
