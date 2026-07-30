import math
from datetime import timedelta
from sqlmodel import select
from remembrance.core.time import utcnow
from remembrance.core.settings import settings
from remembrance.models.tables import MemoryItem
from remembrance.storage.db import get_session


def apply_forgetting():
    now = utcnow()
    with get_session() as s:
        for m in s.exec(select(MemoryItem).where(MemoryItem.status == "active")).all():
            last = m.last_used_at or m.created_at
            days = max(0.0, (now - last).total_seconds() / 86400.0)
            # Ebbinghaus-like: R = exp(-t / S), S grows with importance & use
            strength = 5 + 20 * m.importance + 2 * math.log1p(m.use_count)
            m.decay_score = math.exp(-days / strength)
            if m.tier == "working" and days > settings.WORKING_MEMORY_TTL_DAYS \
                    and m.helpful_count == 0:
                m.status = "archived"
            s.add(m)
        s.commit()
