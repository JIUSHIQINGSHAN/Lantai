"""
lantai/cognition/lifecycle.py
Knowledge Lifecycle Manager（v0.4）
"""

from __future__ import annotations

from sqlmodel import Session

from lantai.core.time import utcnow
from lantai.models.tables import LifecycleStatus, MemoryItem

WEAKENED_THRESHOLD = 0.5
RETIRED_THRESHOLD = 0.3
RETIRED_DECAY_THRESHOLD = 0.2


class LifecycleTransitionError(Exception):
    """非法状态转移"""


class KnowledgeLifecycleManager:
    """
    管理 MemoryItem 的知识生命周期状态转移。

    状态机：
        candidate ──promote()──→ Active
        Active ────weaken()────→ Weakened   (confidence < WEAKENED_THRESHOLD)
        Active/Weakened ─supersede()──→ Superseded
        Weakened/Superseded ─retire()─→ Retired

    所有转移幂等，不 commit（由调用方决定事务边界）。
    """

    WEAKENED_THRESHOLD = WEAKENED_THRESHOLD
    RETIRED_THRESHOLD = RETIRED_THRESHOLD

    def __init__(self, db: Session):
        self.db = db

    def promote(self, item: MemoryItem) -> MemoryItem:
        """candidate → Active（Candidate ≠ Knowledge：必须经过审查闸门）"""
        if item.status != "candidate":
            raise LifecycleTransitionError(
                f"promote() 只接受 status='candidate'，当前 status='{item.status}'。"
                "Candidate != Knowledge：晋升必须经过审查闸门。"
            )
        item.status = "active"
        item.lifecycle_status = LifecycleStatus.ACTIVE
        item.updated_at = utcnow()
        self.db.add(item)
        return item

    def weaken(self, item: MemoryItem) -> MemoryItem:
        """Active → Weakened（置信度下降信号，幂等）"""
        if item.lifecycle_status in (LifecycleStatus.SUPERSEDED, LifecycleStatus.RETIRED):
            return item
        if item.lifecycle_status == LifecycleStatus.WEAKENED:
            return item
        item.lifecycle_status = LifecycleStatus.WEAKENED
        if item.weakened_at is None:
            item.weakened_at = utcnow()
        item.updated_at = utcnow()
        self.db.add(item)
        return item

    def supersede(self, old_item: MemoryItem, new_item: MemoryItem) -> MemoryItem:
        """用 new_item 取代 old_item（幂等）"""
        if old_item.lifecycle_status in (LifecycleStatus.SUPERSEDED, LifecycleStatus.RETIRED):
            return old_item
        if old_item.id == new_item.id:
            raise LifecycleTransitionError("不能用记忆取代自身")
        old_item.lifecycle_status = LifecycleStatus.SUPERSEDED
        old_item.superseded_by = new_item.id
        old_item.superseded_at = utcnow()
        old_item.updated_at = utcnow()
        self.db.add(old_item)
        return old_item

    def retire(self, item: MemoryItem) -> MemoryItem:
        """Weakened/Superseded → Retired（幂等；Active 不可直接退役）"""
        if item.lifecycle_status == LifecycleStatus.RETIRED:
            return item
        if item.lifecycle_status == LifecycleStatus.ACTIVE:
            raise LifecycleTransitionError(
                "Active 记忆不能直接退役。请先调用 weaken() 或 supersede()。"
            )
        item.lifecycle_status = LifecycleStatus.RETIRED
        item.retired_at = utcnow()
        item.updated_at = utcnow()
        self.db.add(item)
        return item

    def scan_and_weaken(self, items: list[MemoryItem]) -> list[MemoryItem]:
        """批量扫描：confidence < WEAKENED_THRESHOLD 且 Active 的记忆自动 weaken"""
        weakened = []
        for item in items:
            if (
                item.lifecycle_status == LifecycleStatus.ACTIVE
                and (item.confidence or 1.0) < self.WEAKENED_THRESHOLD
            ):
                self.weaken(item)
                weakened.append(item)
        return weakened

    def scan_and_retire(
        self, items: list[MemoryItem], decay_threshold: float = RETIRED_DECAY_THRESHOLD
    ) -> list[MemoryItem]:
        """批量扫描：Weakened/Superseded 且 decay_score 极低的记忆自动退役"""
        retired = []
        for item in items:
            if (
                item.lifecycle_status in (LifecycleStatus.WEAKENED, LifecycleStatus.SUPERSEDED)
                and (item.decay_score or 1.0) < decay_threshold
            ):
                self.retire(item)
                retired.append(item)
        return retired
