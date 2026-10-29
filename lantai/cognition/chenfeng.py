"""尘封（Chenfeng · 积尘封藏，不弃不取）：认知层冷热分级管理与自动封藏策略引擎。

管理 MemoryItem 的冷热分级存储与生命周期推进。
识别低衰减分、长期未访问及已退役的冷数据，安全批量转入 `archived`（尘封状态），
并自动写入 MemoryCheckpoint 快照底本保障秒级可逆与审计追溯。
全程坚守行级 ACL 租户隔离，杜绝越权与脏写。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select
from ulid import ULID

from lantai.core.acl import viewer_of
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import LifecycleStatus, MemoryCheckpoint, MemoryItem
from lantai.services.record_ops_service import STATUS_ACTIVE, STATUS_ARCHIVED, audit_event
from lantai.storage import db


class ChenfengEngine:
    """尘封·冷热分级管理与封藏策略引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _owned_scope(self):
        """租户行级安全过滤条件。"""
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        viewer = viewer_of(principal)
        return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))

    def evaluate_cold_candidates(
        self,
        decay_threshold: float = 0.05,
        inactive_days: int = 30,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """多维扫描冷数据候选集。

        判别维度：
        1. 艾宾浩斯时效衰减率 (decay_score < decay_threshold)；
        2. 长期未访问且零使用 (last_used_at/created_at 超过 inactive_days 且 use_count == 0)；
        3. 生命周期显式退役 (lifecycle_status == LifecycleStatus.RETIRED)；
        - 铁律（procedural）天然豁免封藏。
        - 仅对 active 状态条目执行检测。
        """
        eval_now = now or utcnow()
        if eval_now.tzinfo is None:
            eval_now = eval_now.replace(tzinfo=UTC)

        def _scan(s: Session) -> list[dict[str, Any]]:
            stmt = select(MemoryItem).where(MemoryItem.status == STATUS_ACTIVE)
            scope = self._owned_scope()
            if scope is not None:
                stmt = stmt.where(scope)

            items = s.exec(stmt).all()
            cold_list: list[dict[str, Any]] = []

            for item in items:
                # 铁律天然豁免
                if item.decay_class == "procedural":
                    continue

                reasons: list[str] = []

                # 规则 1: 极低 decay_score
                if item.decay_score is not None and item.decay_score < decay_threshold:
                    reasons.append(f"decay_score({item.decay_score:.3f}) < {decay_threshold}")

                # 规则 2: 长期未访问且 0 使用
                last_time = item.last_used_at or item.created_at
                if last_time:
                    if last_time.tzinfo is None:
                        last_time = last_time.replace(tzinfo=UTC)
                    idle_days = (eval_now - last_time).total_seconds() / 86400.0
                    if idle_days > inactive_days and (item.use_count or 0) == 0:
                        reasons.append(
                            f"idle_days({idle_days:.1f}) > {inactive_days} & use_count=0"
                        )

                # 规则 3: 生命周期处于 retired 状态
                if item.lifecycle_status == LifecycleStatus.RETIRED:
                    reasons.append("lifecycle_status=retired")

                if reasons:
                    cold_list.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "lane": item.lane,
                            "decay_score": item.decay_score,
                            "use_count": item.use_count,
                            "lifecycle_status": item.lifecycle_status,
                            "cold_reasons": reasons,
                        }
                    )
            return cold_list

        if self.session is not None:
            return _scan(self.session)
        with db.get_session() as s:
            return _scan(s)

    def archive_cold_memories(
        self,
        memory_ids: list[str],
        reason: str = "chenfeng_cold_archive",
    ) -> dict[str, Any]:
        """批量执行自动封藏：active -> archived 并记录快照底本。"""
        if not memory_ids:
            return {"ok": True, "archived_count": 0, "archived_ids": [], "failed_ids": []}

        def _archive(s: Session) -> dict[str, Any]:
            stmt = select(MemoryItem).where(MemoryItem.id.in_(memory_ids))
            scope = self._owned_scope()
            if scope is not None:
                stmt = stmt.where(scope)

            owned_items = {item.id: item for item in s.exec(stmt).all()}
            archived_ids: list[str] = []
            failed_ids: list[str] = []

            for mid in memory_ids:
                item = owned_items.get(mid)
                # 权限隔离或不存在或非 active
                if item is None or item.status != STATUS_ACTIVE:
                    failed_ids.append(mid)
                    continue

                # 记录变更前快照
                before_snapshot = item.model_dump(mode="json")

                # 推进状态
                item.status = STATUS_ARCHIVED
                item.updated_at = utcnow()

                after_snapshot = item.model_dump(mode="json")

                # 写入底本快照 (MemoryCheckpoint)
                cp = MemoryCheckpoint(
                    id=f"cp_{ULID()}",
                    memory_id=item.id,
                    version=item.version or 1,
                    before=before_snapshot,
                    after=after_snapshot,
                    trigger="chenfeng_archive",
                    created_at=utcnow(),
                )
                s.add(cp)
                s.add(item)

                # 笔削审计
                audit_event(
                    s,
                    memory_id=item.id,
                    action="archive",
                    actor=viewer_of(self.principal),
                    reason=reason,
                    content=item.content,
                    version_at=item.version or 1,
                )
                archived_ids.append(item.id)

            s.commit()
            logger.info(
                "尘封：批量封藏完成，成功 %d 条，失败/阻断 %d 条",
                len(archived_ids),
                len(failed_ids),
            )
            return {
                "ok": True,
                "archived_count": len(archived_ids),
                "archived_ids": archived_ids,
                "failed_ids": failed_ids,
            }

        if self.session is not None:
            return _archive(self.session)
        with db.get_session() as s:
            return _archive(s)
