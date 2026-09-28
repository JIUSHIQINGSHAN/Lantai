import math

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.storage import db


def _lane_strength(importance: float, use_count: int, lane: str) -> float:
    """按 lane profile 计算记忆保持强度 S"""
    profile = settings.LANE_DECAY_PROFILES.get(lane, settings.LANE_DECAY_PROFILES["general"])
    base_s = profile["base_s"]
    boost = profile["importance_boost"]
    return base_s + boost * importance + 2 * math.log1p(use_count)


def _forgetting_scope(principal):
    """遗忘候选集的归属条件（票 .scratch/readside-gaps/12）。

    admin / `principal=None` → None（不过滤）；否则
    `user_id == viewer OR IS NULL`。NULL 口径同票 03/04/06/09/10：
    真实库 615 行 NULL 属主 memoryitem，判不可见会让单人部署下的
    衰减与自动归档整体空转。

    `principal=None`（定时任务）保持全表——衰减是系统行为，
    收窄成空转会让 archive 门槛永不触发。口径刻意如此，见票据口径 4。
    """
    if principal is None:
        return None
    if bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))


def apply_forgetting(principal=None):
    """衰减 + 自动归档。

    - 计算每条记忆的 decay_score（指数衰减）
    - 跳过 |Δdecay| < 0.001 的更新，减少数据库无谓写入 (Ticket 2.4 [DD-06])
    - 分批 commit 减少 WAL 锁竞争 (Ticket 2.4 [DD-06])
    - decay 低于 ARCHIVE_DECAY_THRESHOLD 时自动转 archived
    - working memory 超过 TTL 且无帮助时转 archived
    - archived 记忆不参与检索（WHERE status='active'），但物理不删

    归属（票 .scratch/readside-gaps/12）：此前候选集是全表，一个身份都不取——
    任何持 key 者都能把**别人**的记忆改成 archived，而 archived 没有 undo
    入口。宁 miss 不脏写：收窄后没有可处理的记忆就空跑，不动别人的。
    """
    now = utcnow()
    batch_size = 100
    with db.get_session() as s:
        batch_count = 0
        q = select(MemoryItem).where(MemoryItem.status == "active")
        scope = _forgetting_scope(principal)
        if scope is not None:
            q = q.where(scope)
        for m in s.exec(q).all():
            changed = False
            # procedural 永不衰减：跳过衰减与归档判定，铁律天然浮顶
            if m.decay_class == "procedural":
                if m.decay_score != 1.0:
                    m.decay_score = 1.0
                    changed = True
            else:
                last = m.last_used_at or m.created_at
                if last.tzinfo is not None and now.tzinfo is None:
                    last = last.replace(tzinfo=None)
                elif last.tzinfo is None and now.tzinfo is not None:
                    last = last.replace(tzinfo=now.tzinfo)
                days = max(0.0, (now - last).total_seconds() / 86400.0)
                strength = _lane_strength(m.importance, m.use_count, m.lane)
                new_decay = math.exp(-days / strength)

                # Ticket 2.4 [DD-06]: 跳过极微小更新
                if abs(m.decay_score - new_decay) >= 0.001:
                    m.decay_score = new_decay
                    changed = True

                # 自动归档：decay 极低 或 working memory 过期且无用
                if (
                    m.decay_score < settings.ARCHIVE_DECAY_THRESHOLD
                    and m.status != "archived"
                    or (
                        m.tier == "working"
                        and days > settings.WORKING_MEMORY_TTL_DAYS
                        and m.helpful_count == 0
                        and m.status != "archived"
                    )
                ):
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
