"""尘封（Chenfeng · 积尘封藏，不弃不取）：认知层冷热分级管理与自动封藏策略引擎。

管理 MemoryItem 的冷热分级存储与生命周期推进。
识别低衰减分、长期未访问及已退役的冷数据，安全批量转入 `archived`（尘封状态），
并自动写入 MemoryCheckpoint 快照底本保障秒级可逆与审计追溯。
全程坚守行级 ACL 租户隔离，杜绝越权与脏写。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, func, select
from ulid import ULID

from lantai.core.acl import ensure_can_delete, viewer_of
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import LifecycleStatus, MemoryCheckpoint, MemoryItem
from lantai.services.record_ops_service import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    audit_event,
    unarchive_memory,
)
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

    def search_archived(
        self,
        query: str = "",
        lane: str = "",
        scene: str = "",
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """专用于检索与翻阅尘封（archived）记忆。

        严守租户隔离，过滤出 status='archived' 的条目并支持关键词、分轨、场景及时间范围过滤。
        """

        def _search(s: Session) -> dict[str, Any]:
            stmt = select(MemoryItem).where(MemoryItem.status == STATUS_ARCHIVED)
            scope = self._owned_scope()
            if scope is not None:
                stmt = stmt.where(scope)

            if lane:
                stmt = stmt.where(MemoryItem.lane == lane)
            if scene:
                stmt = stmt.where(MemoryItem.scene_id == scene)
            if time_from:
                stmt = stmt.where(MemoryItem.created_at >= time_from)
            if time_to:
                stmt = stmt.where(MemoryItem.created_at <= time_to)
            if query:
                # 兼容多关键词模糊查询与空格分词
                for term in query.split():
                    term_clean = term.strip()
                    if term_clean:
                        stmt = stmt.where(col(MemoryItem.content).contains(term_clean))

            # 统计符合条件的条目总数
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(s.exec(count_stmt).one() or 0)

            # 分页与时间倒序排序
            stmt = stmt.order_by(
                col(MemoryItem.updated_at).desc(), col(MemoryItem.created_at).desc()
            )
            stmt = stmt.limit(limit).offset(offset)

            items = s.exec(stmt).all()
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [item.model_dump(mode="json") for item in items],
            }

        if self.session is not None:
            return _search(self.session)
        with db.get_session() as s:
            return _search(s)

    def unarchive_memory(
        self,
        memory_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """解封已尘封记忆：验证属主权限并委托 record_ops_service 执行恢复与双层在场核验。"""

        def _unarchive(s: Session) -> dict[str, Any]:
            item = s.get(MemoryItem, memory_id)
            if item is None:
                return {"ok": False, "error": "memory not found"}

            # 行级 ACL 租户防越权校验
            ensure_can_delete(
                self.principal,
                resource_user_id=item.user_id,
                resource_tenant_id=item.tenant_id,
                lane=item.lane,
            )

            actor = viewer_of(self.principal)
            return unarchive_memory(memory_id=memory_id, actor=actor, reason=reason, session=s)

        if self.session is not None:
            return _unarchive(self.session)
        with db.get_session() as s:
            return _unarchive(s)


def search_archived_memories(
    query: str = "",
    lane: str = "",
    scene: str = "",
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    principal=None,
    session: Session | None = None,
) -> dict[str, Any]:
    """快捷检索尘封档案函数。"""
    engine = ChenfengEngine(session=session, principal=principal)
    return engine.search_archived(
        query=query,
        lane=lane,
        scene=scene,
        time_from=time_from,
        time_to=time_to,
        limit=limit,
        offset=offset,
    )


def unarchive_memory_entry(
    memory_id: str,
    reason: str = "",
    principal=None,
    session: Session | None = None,
) -> dict[str, Any]:
    """快捷解封尘封记忆函数。"""
    engine = ChenfengEngine(session=session, principal=principal)
    return engine.unarchive_memory(memory_id=memory_id, reason=reason)
