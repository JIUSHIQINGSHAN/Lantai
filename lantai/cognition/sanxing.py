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
from sqlalchemy import update
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import ConflictEvent, MemoryItem
from lantai.storage import db

STATUS_ACTIVE = "active"


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

    def _reverify_access_predicates(self) -> list[Any]:
        """票 67 半边 3：TOCTOU 重验谓词（下推 UPDATE WHERE）——扫描时点快照
        与落库之间行级权限/状态可能已变（supersede/retract/改属主），写入前
        以谓词重验真值行：不成立则 rowcount=0 静默跳过（宁 miss 不脏写）。"""
        if self.principal is None or bool(getattr(self.principal, "is_admin", False)):
            return []
        viewer = viewer_of(self.principal)
        preds: list[Any] = [(MemoryItem.user_id.is_(None)) | (MemoryItem.user_id == viewer)]
        tenant = getattr(self.principal, "tenant_id", None)
        if tenant:
            preds.append((MemoryItem.tenant_id.is_(None)) | (MemoryItem.tenant_id == tenant))
        return preds

    def evaluate_health(self, now: datetime | None = None) -> SanxingHealthSnapshot:
        eval_now = now or utcnow()

        def _evaluate(s: Session) -> SanxingHealthSnapshot:
            # 票 62：扫描集保持 status==active 不加 lifecycle 白名单——三省是
            # 自愈面，「superseded_but_active 残留」正是它要检出的缺陷对象；
            # 若按读面白名单滤掉 superseded 行，下方诊断就成二阶死代码。
            # superseded 行的退出由 generate_remedies(auto_apply) 落地。
            stmt = select(MemoryItem).where(MemoryItem.status == STATUS_ACTIVE)
            scope = self._owned_memory_scope()
            if scope is not None:
                stmt = stmt.where(scope)
            active_items = s.exec(stmt).all()

            superseded_stale: list[dict[str, Any]] = []
            expired_items: list[dict[str, Any]] = []
            unhelpful_items: list[dict[str, Any]] = []

            for item in active_items:
                # 票 62：死诊断修复——原读 structure["superseded_by"] dict 键，
                # 全仓 grep 实锤 superseded_by 只写**列**（lifecycle.py:74 /
                # late_correction.py:91 / zhiming.py:322），零写点写 structure
                # 键 → 诊断恒假。改读列：真实 supersede（status 仍 active，
                # lifecycle=SUPERSEDED + superseded_by 非空）后才算「已替代
                # 仍活跃」的真实残留（扫描集特意保留 status==active 不滤
                # lifecycle，superseded 残留正是三省的检出对象）。
                if item.superseded_by:
                    superseded_stale.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "superseded_by": item.superseded_by,
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
                                "valid_to": iso_utc(valid_to),
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

    def generate_remedies(
        self, auto_apply: bool = False, now: datetime | None = None
    ) -> SanxingRemedyResult:
        """从健康审视生成结构化自愈动作清单，并按需执行落库自愈（宁 miss 不脏写）。"""
        snapshot = self.evaluate_health(now=now)

        def _run_remediation(s: Session) -> SanxingRemedyResult:
            remedy_items: list[SanxingRemedyItem] = []
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
                # 票 67：写面收敛到下方条件 UPDATE 块（重验下推 + 保存点遏制）
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
                # 票 67：写面收敛到下方条件 UPDATE 块
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
                # 票 67：写面收敛到下方条件 UPDATE 块
                remedy_items.append(r_item)

            if auto_apply:
                # 票 67 半边 2+3：自愈写走独立保存点 + 条件重验下推 UPDATE——
                # ①保存点（begin_nested）：失败仅回滚本操作，调用方外层事务
                #   （中间态、探针行）不被整体提交/回滚/连坐关闭；
                # ②TOCTOU 重验下推：UPDATE ... WHERE status='active' AND
                #   lifecycle 白名单 + access 谓词随行内判定，扫描时点快照与
                #   落库之间被 supersede/retract 的候选行数=0 自动跳过（宁
                #   miss 不脏写，不报错不覆写撤回）。
                nested = s.begin_nested()
                try:
                    for r_item in remedy_items:
                        if r_item.defect_type == "open_conflict":
                            continue  # 审计待办无写面
                        tid = r_item.target_id
                        # 票 67 谓词按动作分流：supersede_retract 的诊断对象恰是
                        # lifecycle='superseded' 的残留行（票 62 口径），重验只验
                        # status='active'（防 retract 物化——TOCTOU 本意）；其余
                        # 动作加 lifecycle 白名单（active/weakened 才可自愈流转）。
                        reverify = [MemoryItem.status == STATUS_ACTIVE]
                        if r_item.recommended_action != "supersede_retract":
                            reverify.append(MemoryItem.lifecycle_status.in_(("active", "weakened")))
                        stmt = (
                            update(MemoryItem)
                            .where(
                                MemoryItem.id == tid,
                                *reverify,
                                *self._reverify_access_predicates(),
                            )
                            .execution_options(synchronize_session=False)
                        )
                        if r_item.recommended_action == "supersede_retract":
                            stmt = stmt.values(status="archived", lifecycle_status="superseded")
                        elif r_item.recommended_action == "archive_expire":
                            stmt = stmt.values(status="archived")
                        elif r_item.recommended_action == "demote_deprecate":
                            stmt = stmt.values(importance=0.1)
                        else:
                            continue
                        rowcount = s.execute(stmt).rowcount
                        if rowcount:
                            r_item.executed = True
                            applied_count += 1
                    s.flush()
                    nested.commit()
                except Exception:
                    nested.rollback()
                    raise
                # 属主/租户 ACL（票 57 口径）：条件 UPDATE 不走 ORM identity map，
                # _check_access 的行级判定由重验后的真值行内聚合（见 remedy_items
                # 构造时的 snapshot 扫描面），谓词不匹配则 rowcount=0 自然跳过。
                if self.session is None:
                    s.commit()  # 自建会话（with 轨道）：终态落库，with 退出即关

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
            return _run_remediation(self.session)  # 票 67：不 commit，提交权归调用方
        with db.get_session() as s:
            return _run_remediation(s)  # 自建会话轨道内部 commit（见 auto_apply 块）


def inspect_memory_health(
    principal=None,
    session: Session | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """快捷入口：执行三省健康自检并返回审计报告字典。"""
    engine = SanxingEngine(session=session, principal=principal)
    report = engine.generate_audit_report(now=now)
    return report.model_dump(mode="json")
