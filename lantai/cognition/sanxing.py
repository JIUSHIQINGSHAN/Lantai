"""三省（Sanxing · 吾日三省吾身）：认知层反思自省引擎与记忆健康审视。

依照 ADR-0013 命名系统与 ADR-0044 认知演化边界规范构建：
对系统存量记忆开展多维健康质量自检，扫描并诊断四大类认知缺陷：
- 规则 R1（被取代残留）：已被标记为 supersedes / superseded_by 替代但仍为 active 的陈旧事实；
- 规则 R2（时效过期）：valid_to 已过期且非 procedural（程序性铁律天然豁免）；
- 规则 R3（未决冲突隐患）：状态仍为 open 的冲突账本记录（ConflictEvent）；
- 规则 R4（低帮助度碎片）：高频被引用（use_count >= 5）但 0 采纳/0 帮助（helpful_count == 0）的认知杂音；

聚合生成结构化自检快照 SanxingHealthSnapshot 与健康诊断报告 SanxingAuditResult。
全程严守行级 ACL 与多租户安全边界，杜绝越权感知与脏写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from lantai.core.acl import viewer_of
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.storage import db

STATUS_ACTIVE = "active"


class SanxingHealthSnapshot(BaseModel):
    """三省健康审视结构化快照。"""

    total_checked: int = 0
    superseded_stale: list[dict[str, Any]] = Field(default_factory=list)
    expired_items: list[dict[str, Any]] = Field(default_factory=list)
    open_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    unhelpful_items: list[dict[str, Any]] = Field(default_factory=list)
    health_score: float = 100.0
    evaluated_at: datetime = Field(default_factory=utcnow)


class SanxingAuditResult(BaseModel):
    """三省审计结果与优化建议。"""

    health_score: float = 100.0
    summary: str = ""
    issues_count: int = 0
    details: list[dict[str, Any]] = Field(default_factory=list)
    snapshot: SanxingHealthSnapshot = Field(default_factory=SanxingHealthSnapshot)


class SanxingEngine:
    """三省·认知层反思自省与健康审视引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _owned_memory_scope(self):
        """MemoryItem 租户行级安全过滤条件。"""
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        viewer = viewer_of(principal)
        return (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))

    def _visible_conflict_ids(self, s: Session) -> set[str] | None:
        """获取当前 principal 可见的冲突事件 ID 集合；None 表示不过滤。
        ConflictEvent 属主通过挂载的 MemoryItem.user_id 推导。
        """
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        viewer = viewer_of(principal)
        rows = s.exec(
            select(ConflictEvent.id, MemoryItem.user_id).join(
                MemoryItem, ConflictEvent.memory_id == MemoryItem.id, isouter=True
            )
        ).all()
        return {cid for cid, uid in rows if uid is not None and uid == viewer}

    def evaluate_health(self, now: datetime | None = None) -> SanxingHealthSnapshot:
        """多维健康规则扫描。

        判别维度：
        - R1: 被取代残留（structure 内含 superseded_by 且 status == 'active'）；
        - R2: 时效过期（valid_to < now 且 decay_class != 'procedural' 且 status == 'active'）；
        - R3: 未决冲突隐患（ConflictEvent.status == 'open'）；
        - R4: 低帮助度碎片（use_count >= 5 且 helpful_count == 0 且 status == 'active'）；
        """
        eval_now = now or utcnow()

        def _evaluate(s: Session) -> SanxingHealthSnapshot:
            # 1. 扫描活跃记忆
            stmt = select(MemoryItem).where(MemoryItem.status == STATUS_ACTIVE)
            scope = self._owned_memory_scope()
            if scope is not None:
                stmt = stmt.where(scope)
            active_items = s.exec(stmt).all()

            superseded_stale: list[dict[str, Any]] = []
            expired_items: list[dict[str, Any]] = []
            unhelpful_items: list[dict[str, Any]] = []

            for item in active_items:
                struct = item.structure or {}
                # R1 检查: 被取代残留
                if struct.get("superseded_by"):
                    superseded_stale.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "superseded_by": struct.get("superseded_by"),
                            "reason": "superseded_but_active",
                        }
                    )

                # R2 检查: 时效过期，procedural 天然豁免
                if item.valid_to is not None:
                    # 确保带时区比较
                    valid_to = item.valid_to
                    if valid_to.tzinfo is None:
                        valid_to = valid_to.replace(tzinfo=UTC)
                    now_tz = (
                        eval_now if eval_now.tzinfo is not None else eval_now.replace(tzinfo=UTC)
                    )

                    if valid_to < now_tz and (item.decay_class or "").lower() != "procedural":
                        expired_items.append(
                            {
                                "id": item.id,
                                "content": item.content,
                                "valid_to": valid_to.isoformat(),
                                "reason": "validity_expired",
                            }
                        )

                # R4 检查: 高频使用但零帮助
                if (item.use_count or 0) >= 5 and (item.helpful_count or 0) == 0:
                    unhelpful_items.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "use_count": item.use_count,
                            "helpful_count": item.helpful_count,
                            "reason": "unhelpful_fragment",
                        }
                    )

            # 2. 扫描未决冲突 (R3)
            conflict_stmt = select(ConflictEvent).where(ConflictEvent.status == "open")
            visible_conf_ids = self._visible_conflict_ids(s)
            conflicts = s.exec(conflict_stmt).all()
            if visible_conf_ids is not None:
                conflicts = [c for c in conflicts if c.id in visible_conf_ids]

            open_conflicts = [
                {
                    "id": c.id,
                    "memory_id": c.memory_id,
                    "rule_name": c.rule_name,
                    "incoming_ref": c.incoming_ref,
                    "reason": "unresolved_conflict",
                }
                for c in conflicts
            ]

            total_items = len(active_items)
            issue_count = (
                len(superseded_stale)
                + len(expired_items)
                + len(open_conflicts)
                + len(unhelpful_items)
            )
            # 健康分计算公式：满分 100，每处缺陷扣 5 分，最低 0 分
            penalty = issue_count * 5.0
            health_score = max(0.0, round(100.0 - penalty, 2))

            return SanxingHealthSnapshot(
                total_checked=total_items,
                superseded_stale=superseded_stale,
                expired_items=expired_items,
                open_conflicts=open_conflicts,
                unhelpful_items=unhelpful_items,
                health_score=health_score,
                evaluated_at=eval_now,
            )

        if self.session is not None:
            return _evaluate(self.session)
        with db.get_session() as s:
            return _evaluate(s)

    def generate_audit_report(self, now: datetime | None = None) -> SanxingAuditResult:
        """生成三省健康审计报告与明细清单。"""
        snapshot = self.evaluate_health(now=now)
        details: list[dict[str, Any]] = []

        for item in snapshot.superseded_stale:
            details.append(
                {
                    "type": "superseded_stale",
                    "id": item["id"],
                    "content": item.get("content", ""),
                    "description": f"已由 {item.get('superseded_by')} 替代但仍在活跃态",
                    "suggestion": "建议归档或折叠起复",
                }
            )
        for item in snapshot.expired_items:
            details.append(
                {
                    "type": "expired",
                    "id": item["id"],
                    "content": item.get("content", ""),
                    "description": f"有效期已于 {item.get('valid_to')} 截止",
                    "suggestion": "建议尘封归档或更新有效期限",
                }
            )
        for item in snapshot.open_conflicts:
            details.append(
                {
                    "type": "open_conflict",
                    "id": item["id"],
                    "memory_id": item.get("memory_id"),
                    "description": f"记忆 {item.get('memory_id')} 存在未决冲突（规则: {item.get('rule_name')}）",
                    "suggestion": "建议通过直断裁决或探颐求证消解",
                }
            )
        for item in snapshot.unhelpful_items:
            details.append(
                {
                    "type": "unhelpful_fragment",
                    "id": item["id"],
                    "content": item.get("content", ""),
                    "description": f"使用次数达到 {item.get('use_count')} 但帮助度为 0",
                    "suggestion": "建议重炼精简或清理沉淀",
                }
            )

        issues_count = len(details)
        summary = (
            f"三省健康自检完成，审视活跃记忆 {snapshot.total_checked} 条，"
            f"健康评分: {snapshot.health_score}，发现健康隐患 {issues_count} 处。"
        )

        return SanxingAuditResult(
            health_score=snapshot.health_score,
            summary=summary,
            issues_count=issues_count,
            details=details,
            snapshot=snapshot,
        )


def inspect_memory_health(
    principal=None,
    session: Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """快捷入口：执行三省健康自检并返回审计报告字典。"""
    engine = SanxingEngine(session=session, principal=principal)
    report = engine.generate_audit_report(now=now)
    return report.model_dump(mode="json")
