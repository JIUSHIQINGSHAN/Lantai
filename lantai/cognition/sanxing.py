"""三省（Sanxing · 吾日三省吾身）：认知层反思自省引擎与记忆健康自愈闭环 (ADR-0079)。

依照 ADR-0013 命名系统、ADR-0044 认知演化边界与 ADR-0079 自愈闭环规范构建：
1. 多维健康质量诊断（四大缺陷：被取代残留、时效过期、未决冲突、低帮助度碎片）；
2. 结构化自愈动作推导（SanxingRemedyItem / SanxingRemedyResult）；
3. 联动状态机执行就地修复（supersede_retract 撤回陈旧条目、archive_expire 归档过期条目、demote_deprecate 降权低效条目）；
4. 全面集成樊篱防注入安全围栏（wrap_as_data）；
5. 落实行级租户与属主安全隔离（宁漏不越权、宁 miss 不脏写）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy.orm.exc import ObjectDeletedError
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.storage import db

STATUS_ACTIVE = "active"


def _norm_valid_to_iso(valid_to: datetime | None) -> str | None:
    """与提名侧同口径的 valid_to 归一(naive 视作 UTC 后 isoformat),供快照串比对。"""
    if valid_to is None:
        return None
    if valid_to.tzinfo is None:
        valid_to = valid_to.replace(tzinfo=UTC)
    return valid_to.isoformat()


class SanxingHealthSnapshot(BaseModel):
    """三省健康审视结构化快照。"""

    total_checked: int = 0
    superseded_stale: list[dict[str, Any]] = PydanticField(default_factory=list)
    expired_items: list[dict[str, Any]] = PydanticField(default_factory=list)
    open_conflicts: list[dict[str, Any]] = PydanticField(default_factory=list)
    unhelpful_items: list[dict[str, Any]] = PydanticField(default_factory=list)
    health_score: float = 100.0
    evaluated_at: datetime = PydanticField(default_factory=utcnow)


class SanxingAuditResult(BaseModel):
    """三省审计结果与优化建议。"""

    health_score: float = 100.0
    summary: str = ""
    issues_count: int = 0
    details: list[dict[str, Any]] = PydanticField(default_factory=list)
    snapshot: SanxingHealthSnapshot = PydanticField(default_factory=SanxingHealthSnapshot)


@dataclass
class SanxingRemedyItem:
    """单项自愈策略与处置动作契约。"""

    target_id: str
    defect_type: str  # superseded_stale | expired | open_conflict | unhelpful
    recommended_action: (
        str  # supersede_retract | archive_expire | arbitrate_dispatch | demote_deprecate
    )
    risk_level: str = "low"  # low | medium | high
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    executed: bool = False
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "defect_type": self.defect_type,
            "recommended_action": self.recommended_action,
            "risk_level": self.risk_level,
            "detail": self.detail,
            "reason": self.reason,
            "executed": self.executed,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class SanxingRemedyResult:
    """三省自省自愈综合审计契约。"""

    total_defects: int
    remedies: list[SanxingRemedyItem] = field(default_factory=list)
    applied_count: int = 0
    health_score_before: float = 100.0
    health_score_after: float = 100.0
    summary: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "total_defects": self.total_defects,
            "applied_count": self.applied_count,
            "health_score_before": round(self.health_score_before, 2),
            "health_score_after": round(self.health_score_after, 2),
            "summary": self.summary,
            "remedies": [r.to_dict(fenced=fenced) for r in self.remedies],
        }


class SanxingEngine:
    """三省·认知层反思自省与健康自愈引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _owned_memory_scope(self):
        """MemoryItem 租户行级安全过滤条件。"""
        principal = self.principal
        if principal is not None and bool(getattr(principal, "is_admin", False)):
            return None
        viewer = viewer_of(principal)
        cond = (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
        tenant = getattr(principal, "tenant_id", None) if principal else None
        if tenant:
            cond = cond & ((MemoryItem.tenant_id == tenant) | (MemoryItem.tenant_id.is_(None)))
        return cond

    def _visible_conflict_ids(self, s: Session) -> set[str] | None:
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

    def _check_access(self, *, owner_id: str | None, tenant_id: str | None) -> bool:
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)
        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False
        return not (owner_id is not None and owner_id != viewer)

    def evaluate_health(self, now: datetime | None = None) -> SanxingHealthSnapshot:
        eval_now = now or utcnow()

        def _evaluate(s: Session) -> SanxingHealthSnapshot:
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
                if struct.get("superseded_by"):
                    superseded_stale.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "superseded_by": struct.get("superseded_by"),
                            "reason": "superseded_but_active",
                            "user_id": item.user_id,
                            "tenant_id": item.tenant_id,
                        }
                    )

                if item.valid_to is not None:
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
                                "user_id": item.user_id,
                                "tenant_id": item.tenant_id,
                            }
                        )

                if (item.use_count or 0) >= 5 and (item.helpful_count or 0) == 0:
                    unhelpful_items.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "use_count": item.use_count,
                            "helpful_count": item.helpful_count,
                            "reason": "unhelpful_fragment",
                            "user_id": item.user_id,
                            "tenant_id": item.tenant_id,
                        }
                    )

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
        snapshot = self.evaluate_health(now=now)
        details: list[dict[str, Any]] = []

        for item in snapshot.superseded_stale:
            details.append(
                {
                    "type": "superseded_stale",
                    "id": item["id"],
                    "content": item.get("content", ""),
                    "description": f"已由 {item.get('superseded_by')} 替代但仍在活跃态",
                    "suggestion": "建议撤销或状态流转",
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
                    "suggestion": "建议降权淘汰或精炼提纯",
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

    def _reverified_remedy_row(
        self, s: Session, defect_type: str, item: dict[str, Any]
    ) -> MemoryItem | None:
        """落库前重取证并重验提名条件(票 67 TOCTOU,宁 miss 不脏写)。

        快照(提名)与落库之间可能隔着另一个写方:行已撤回/归档、superseded_by
        已换人、valid_to 已展期、use/helpful 计数已翻转——任一发生即跳过该候选,
        不报错。refresh 强制真 SELECT,调用方会话身份映射的滞留读也拦不住重验。
        """
        mem = s.get(MemoryItem, item["id"])
        if mem is None:
            return None
        try:
            s.refresh(mem)
        except ObjectDeletedError:
            return None
        if mem.status != STATUS_ACTIVE:
            return None
        if defect_type == "superseded_stale":
            if (mem.structure or {}).get("superseded_by") != item.get("superseded_by"):
                return None
        elif defect_type == "expired":
            if _norm_valid_to_iso(mem.valid_to) != item.get("valid_to"):
                return None
        elif defect_type == "unhelpful":
            if (mem.use_count, mem.helpful_count) != (
                item.get("use_count"),
                item.get("helpful_count"),
            ):
                return None
        return mem

    def generate_remedies(
        self, auto_apply: bool = False, now: datetime | None = None
    ) -> SanxingRemedyResult:
        """从健康审视生成结构化自愈动作清单，并按需执行落库自愈（宁 miss 不脏写）。"""
        snapshot = self.evaluate_health(now=now)

        def _run_remediation(s: Session) -> SanxingRemedyResult:
            remedy_items: list[SanxingRemedyItem] = []
            apply_plan: list[tuple[SanxingRemedyItem, dict[str, Any]]] = []
            applied_count = 0

            # 1. 处置陈旧被取代记忆 -> supersede_retract (置为 superseded 或撤回)
            for item in snapshot.superseded_stale:
                tid = item["id"]
                content = item.get("content", "")
                fenced = wrap_as_data(content, item_id=tid)
                r_item = SanxingRemedyItem(
                    target_id=tid,
                    defect_type="superseded_stale",
                    recommended_action="supersede_retract",
                    risk_level="low",
                    detail={"superseded_by": item.get("superseded_by")},
                    reason=f"记忆已由 {item.get('superseded_by')} 替代，退出活跃态",
                    fenced_content=fenced,
                )
                if auto_apply:
                    apply_plan.append((r_item, item))
                remedy_items.append(r_item)

            # 2. 处置过期记忆 -> archive_expire (置为 archived)
            for item in snapshot.expired_items:
                tid = item["id"]
                content = item.get("content", "")
                fenced = wrap_as_data(content, item_id=tid)
                r_item = SanxingRemedyItem(
                    target_id=tid,
                    defect_type="expired",
                    recommended_action="archive_expire",
                    risk_level="low",
                    detail={"valid_to": item.get("valid_to")},
                    reason=f"时效过期（截止 {item.get('valid_to')}），休眠转入归档",
                    fenced_content=fenced,
                )
                if auto_apply:
                    apply_plan.append((r_item, item))
                remedy_items.append(r_item)

            # 3. 处置未决冲突 -> arbitrate_dispatch (标记审计待办)
            for item in snapshot.open_conflicts:
                cid = item["id"]
                r_item = SanxingRemedyItem(
                    target_id=cid,
                    defect_type="open_conflict",
                    recommended_action="arbitrate_dispatch",
                    risk_level="medium",
                    detail={"memory_id": item.get("memory_id"), "rule_name": item.get("rule_name")},
                    reason="未决冲突记录，需介入仲裁或探针求证",
                    fenced_content=wrap_as_data(f"冲突事件: {cid}", item_id=cid),
                )
                remedy_items.append(r_item)

            # 4. 处置低效碎片 -> demote_deprecate (重要度降权至 0.1)
            for item in snapshot.unhelpful_items:
                tid = item["id"]
                content = item.get("content", "")
                fenced = wrap_as_data(content, item_id=tid, score=0.1)
                r_item = SanxingRemedyItem(
                    target_id=tid,
                    defect_type="unhelpful",
                    recommended_action="demote_deprecate",
                    risk_level="low",
                    detail={
                        "use_count": item.get("use_count"),
                        "helpful_count": item.get("helpful_count"),
                    },
                    reason="高频使用但零帮助，自省降权淘汰",
                    fenced_content=fenced,
                )
                if auto_apply:
                    apply_plan.append((r_item, item))
                remedy_items.append(r_item)

            if auto_apply and apply_plan:
                # 保存点(票 67):仅本操作提交/回滚,不动调用方外层事务——
                # 调用方会话上的未完成中间写入不再被整体提交;内部失败只回滚本操作。
                with s.begin_nested():
                    for r_item, item in apply_plan:
                        # 落库前重取证重验(票 67 TOCTOU):提名与落库之间行可能
                        # 已被撤回/归档、superseded_by 换人、valid_to 展期、计数
                        # 翻转——条件不再成立即跳过,宁 miss 不脏写。
                        mem = self._reverified_remedy_row(s, r_item.defect_type, item)
                        if mem is None:
                            continue
                        if not self._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
                            continue
                        if r_item.defect_type == "superseded_stale":
                            mem.status = "archived"
                            mem.lifecycle_status = "superseded"
                        elif r_item.defect_type == "expired":
                            mem.status = "archived"
                        else:  # unhelpful
                            mem.importance = 0.1
                        s.add(mem)
                        r_item.executed = True
                        applied_count += 1

            score_before = snapshot.health_score
            # 重新评估自愈后健康分
            remaining_issues = len(remedy_items) - applied_count
            score_after = (
                max(0.0, round(100.0 - remaining_issues * 5.0, 2)) if auto_apply else score_before
            )
            summary = (
                f"三省自愈分析完成：发现缺陷 {len(remedy_items)} 处，已就地修复 {applied_count} 处，"
                f"健康分自愈变迁: {score_before} -> {score_after}。"
            )

            return SanxingRemedyResult(
                total_defects=len(remedy_items),
                remedies=remedy_items,
                applied_count=applied_count,
                health_score_before=score_before,
                health_score_after=score_after,
                summary=summary,
            )

        if self.session is not None:
            # 调用方会话(票 67):保存点只落本操作,外层事务的提交权归调用方
            return _run_remediation(self.session)
        with db.get_session() as s:
            result = _run_remediation(s)
            if auto_apply:
                s.commit()  # 自有会话:保存点已释放,外层提交真实落库
            return result


def inspect_memory_health(
    principal=None,
    session: Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """快捷入口：执行三省健康自检并返回审计报告字典。"""
    engine = SanxingEngine(session=session, principal=principal)
    report = engine.generate_audit_report(now=now)
    return report.model_dump(mode="json")
