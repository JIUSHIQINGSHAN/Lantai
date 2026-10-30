"""知命（Zhiming · ADR-0081）：认知生命全周期演化状态机与动态时效衰变引擎。

出处：《论语·尧曰》「不知命，无以为君子也」、《论语·为政》「五十而知天命」。
职责：
1. 动态半衰期衰变计算（Half-life Decay）与有效置信度推演；
2. 状态机严格转移控制（支持 candidate -> active -> weakened -> superseded -> retired，以及 weakened -> active 逆向复元）；
3. 真实数据库落库持久化与状态变迁追踪（MemoryItem.lifecycle_status）；
4. 行级租户与属主安全隔离（宁漏不越权）；
5. 全面集成樊篱防注入安全围栏（wrap_as_data）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.time import ensure_aware, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import LifecycleStatus, MemoryItem
from lantai.storage import db

DEFAULT_HALF_LIFE_DAYS = 30.0
WEAKENED_THRESHOLD = 0.5
RETIRED_THRESHOLD = 0.2
REJUVENATE_THRESHOLD = 0.65


class LifecyclePhase(StrEnum):
    """知命生命周期阶段枚举。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    WEAKENED = "weakened"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


@dataclass
class ZhimingTransition:
    """知命生命状态转移记录。"""

    memory_id: str
    from_phase: LifecyclePhase
    to_phase: LifecyclePhase
    effective_confidence: float
    decay_factor: float
    reason: str
    superseded_by: str | None = None
    applied: bool = False
    updated_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "from_phase": self.from_phase.value,
            "to_phase": self.to_phase.value,
            "effective_confidence": round(self.effective_confidence, 4),
            "decay_factor": round(self.decay_factor, 4),
            "reason": self.reason,
            "superseded_by": self.superseded_by,
            "applied": self.applied,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class ZhimingItem:
    """知命单条记忆生命状态实体。"""

    memory_id: str
    phase: LifecyclePhase
    base_confidence: float
    effective_confidence: float
    half_life_days: float
    age_days: float
    use_count: int
    helpful_count: int
    user_id: str | None = None
    tenant_id: str | None = None
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "phase": self.phase.value,
            "base_confidence": round(self.base_confidence, 4),
            "effective_confidence": round(self.effective_confidence, 4),
            "half_life_days": round(self.half_life_days, 1),
            "age_days": round(self.age_days, 1),
            "use_count": self.use_count,
            "helpful_count": self.helpful_count,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class ZhimingResult:
    """知命生命周期审查与流转结果。"""

    total_inspected: int
    transitions: list[ZhimingTransition] = field(default_factory=list)
    applied_count: int = 0
    distribution: dict[str, int] = field(default_factory=dict)
    summary: str = ""
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "total_inspected": self.total_inspected,
            "transitions": [t.to_dict() for t in self.transitions],
            "applied_count": self.applied_count,
            "distribution": dict(self.distribution),
            "summary": self.summary,
            "fenced_content": self.fenced_content if fenced else "",
        }


class ZhimingEngine:
    """知命·认知生命周期演化状态机与动态时效衰变引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _check_access(self, *, owner_id: str | None, tenant_id: str | None) -> bool:
        """行级租户与属主安全校验（宁漏不越权）。"""
        if self.principal is None:
            return owner_id in (None, "default")
        if (
            getattr(self.principal, "is_admin", False)
            or getattr(self.principal, "role", None) == "admin"
        ):
            return True
        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)
        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False
        return not (owner_id is not None and owner_id != viewer)

    def compute_effective_confidence(
        self,
        base_confidence: float,
        age_days: float,
        use_count: int = 0,
        helpful_count: int = 0,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    ) -> float:
        """动态半衰期衰变计算模型（Half-life Decay）。

        公式：
        EffectiveConfidence = BaseConfidence * exp(- age_days / effective_half_life)
        其中 effective_half_life = half_life_days * (1 + ln(1 + use_count)) * (1 + 0.2 * helpful_count)
        """
        b_conf = max(0.0, min(1.0, float(base_confidence or 0.5)))
        a_days = max(0.0, float(age_days or 0.0))
        u_cnt = max(0, int(use_count or 0))
        h_cnt = max(0, int(helpful_count or 0))

        # 动态半衰期延伸因子：被多次使用和证实有用的记忆，生命周期显著拉长
        usage_factor = 1.0 + math.log(1.0 + u_cnt)
        helpful_factor = 1.0 + min(2.0, 0.2 * h_cnt)
        effective_half_life = max(1.0, half_life_days * usage_factor * helpful_factor)

        decay = math.exp(-a_days / effective_half_life)
        return max(0.0, min(1.0, b_conf * decay))

    def evaluate_transition_pure(
        self,
        memory_id: str,
        current_phase: LifecyclePhase,
        base_confidence: float,
        age_days: float,
        use_count: int = 0,
        helpful_count: int = 0,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    ) -> ZhimingTransition:
        """纯函数逻辑：评估单条记忆应有的生命周期状态跃迁。"""
        eff_conf = self.compute_effective_confidence(
            base_confidence=base_confidence,
            age_days=age_days,
            use_count=use_count,
            helpful_count=helpful_count,
            half_life_days=half_life_days,
        )
        decay_factor = eff_conf / max(0.001, base_confidence)

        target_phase = current_phase
        reason = "保持当前生命状态"

        if current_phase == LifecyclePhase.ACTIVE:
            if eff_conf < RETIRED_THRESHOLD:
                target_phase = LifecyclePhase.RETIRED
                reason = (
                    f"时效衰变严重（有效置信度 {eff_conf:.3f} < {RETIRED_THRESHOLD}），退役归档"
                )
            elif eff_conf < WEAKENED_THRESHOLD:
                target_phase = LifecyclePhase.WEAKENED
                reason = f"跌破弱化阈值（有效置信度 {eff_conf:.3f} < {WEAKENED_THRESHOLD}），转入弱化观察期"
        elif current_phase == LifecyclePhase.WEAKENED:
            # 弱化状态下的逆向复元（rejuvenate）逻辑：近期有充分正向使用，恢复活力（至少 3 次使用且采纳率高）
            if eff_conf >= REJUVENATE_THRESHOLD or (
                use_count >= 3 and helpful_count >= 3 and helpful_count >= use_count * 0.7
            ):
                target_phase = LifecyclePhase.ACTIVE
                reason = f"正向反馈显著（有效置信度 {eff_conf:.3f} >= {REJUVENATE_THRESHOLD}，采纳 {helpful_count}/{use_count}），成功复元恢复活力"
            elif eff_conf < RETIRED_THRESHOLD:
                target_phase = LifecyclePhase.RETIRED
                reason = f"弱化观察期期满且衰变至临界值（有效置信度 {eff_conf:.3f} < {RETIRED_THRESHOLD}），确认退役"

        return ZhimingTransition(
            memory_id=memory_id,
            from_phase=current_phase,
            to_phase=target_phase,
            effective_confidence=eff_conf,
            decay_factor=decay_factor,
            reason=reason,
            applied=False,
            updated_at=utcnow(),
        )

    def evaluate_transition_for_item(self, item: ZhimingItem) -> ZhimingTransition:
        """根据 ZhimingItem 实体评估状态转移。"""
        return self.evaluate_transition_pure(
            memory_id=item.memory_id,
            current_phase=item.phase,
            base_confidence=item.base_confidence,
            age_days=item.age_days,
            use_count=item.use_count,
            helpful_count=item.helpful_count,
            half_life_days=item.half_life_days,
        )

    def inspect_lifecycle(self, memory_id: str, now: datetime | None = None) -> ZhimingItem:
        """从 DB 读取指定记忆并计算当前生命周期度量。"""
        mid = (memory_id or "").strip()
        ref_now = now or utcnow()

        def _run(s: Session) -> ZhimingItem:
            mem = s.get(MemoryItem, mid)
            if mem is None:
                raise ValueError(f"记忆未找到: {mid}")

            if not self._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
                raise PermissionError(f"无权查看此记忆生命周期（行级隔离）: {mid}")

            # 换算当前生命周期 Phase
            raw_phase = (mem.lifecycle_status or LifecycleStatus.ACTIVE).lower()
            try:
                phase = LifecyclePhase(raw_phase)
            except ValueError:
                phase = LifecyclePhase.ACTIVE

            # 计算年龄
            created = ensure_aware(mem.created_at) or ref_now
            age_days = max(0.0, (ref_now - created).total_seconds() / 86400.0)

            eff_conf = self.compute_effective_confidence(
                base_confidence=mem.confidence,
                age_days=age_days,
                use_count=mem.use_count,
                helpful_count=mem.helpful_count,
            )

            fenced = wrap_as_data(mem.content, item_id=f"zhiming-{mem.id}", score=eff_conf)

            return ZhimingItem(
                memory_id=mem.id,
                phase=phase,
                base_confidence=mem.confidence,
                effective_confidence=eff_conf,
                half_life_days=DEFAULT_HALF_LIFE_DAYS,
                age_days=age_days,
                use_count=mem.use_count,
                helpful_count=mem.helpful_count,
                user_id=mem.user_id,
                tenant_id=mem.tenant_id,
                fenced_content=fenced,
            )

        if self.session is not None:
            return _run(self.session)
        with db.get_session() as s:
            return _run(s)

    def apply_transition(self, transition: ZhimingTransition) -> ZhimingTransition:
        """在 DB 中就地应用状态转移（事务更新）。"""

        def _write(s: Session) -> ZhimingTransition:
            mem = s.get(MemoryItem, transition.memory_id)
            if mem is None:
                raise ValueError(f"记忆未找到: {transition.memory_id}")

            if not self._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
                raise PermissionError(f"无权修改此记忆生命周期（行级隔离）: {transition.memory_id}")

            now = utcnow()
            # 状态转移落地
            mem.lifecycle_status = transition.to_phase.value
            mem.updated_at = now

            if transition.to_phase == LifecyclePhase.ACTIVE:
                # 晋升或复元：确保 status 也为 active
                mem.status = "active"
            elif transition.to_phase == LifecyclePhase.WEAKENED:
                mem.weakened_at = now
            elif transition.to_phase == LifecyclePhase.SUPERSEDED:
                mem.superseded_at = now
                if transition.superseded_by:
                    mem.superseded_by = transition.superseded_by
            elif transition.to_phase == LifecyclePhase.RETIRED:
                mem.retired_at = now
                mem.status = "archived"

            s.add(mem)
            s.commit()
            s.refresh(mem)

            transition.applied = True
            transition.updated_at = now
            return transition

        if self.session is not None:
            return _write(self.session)
        with db.get_session() as s:
            return _write(s)

    def transition_memory(
        self,
        memory_id: str,
        target_phase: LifecyclePhase,
        reason: str = "",
        superseded_by: str | None = None,
        apply: bool = False,
    ) -> ZhimingTransition:
        """显式请求将某记忆跃迁至目标生命周期阶段。"""
        item = self.inspect_lifecycle(memory_id)
        decay_factor = item.effective_confidence / max(0.001, item.base_confidence)
        trans = ZhimingTransition(
            memory_id=item.memory_id,
            from_phase=item.phase,
            to_phase=target_phase,
            effective_confidence=item.effective_confidence,
            decay_factor=decay_factor,
            reason=reason or f"显式转移至 {target_phase.value}",
            superseded_by=superseded_by,
            applied=False,
            updated_at=utcnow(),
        )
        if apply:
            return self.apply_transition(trans)
        return trans

    def run_lifecycle_audit(
        self,
        auto_apply: bool = False,
        limit: int = 100,
        now: datetime | None = None,
    ) -> ZhimingResult:
        """全库或当前租户范围内执行知识生命周期巡检与状态机推进。"""
        ref_now = now or utcnow()

        def _run(s: Session) -> ZhimingResult:
            stmt = select(MemoryItem).where(MemoryItem.status != "deleted")
            # 行级 ACL 过滤（宁漏不越权：租户与用户归属严格绑定）
            if self.principal is not None and not getattr(self.principal, "is_admin", False):
                viewer = viewer_of(self.principal)
                current_tenant = getattr(self.principal, "tenant_id", None)
                if current_tenant:
                    stmt = stmt.where(
                        (MemoryItem.tenant_id == current_tenant)
                        & ((MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None)))
                    )
                else:
                    stmt = stmt.where(
                        (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
                    )

            items = s.exec(stmt.limit(limit)).all()

            transitions: list[ZhimingTransition] = []
            applied_count = 0
            distribution: dict[str, int] = {}

            for m in items:
                raw_phase = (m.lifecycle_status or LifecycleStatus.ACTIVE).lower()
                try:
                    phase = LifecyclePhase(raw_phase)
                except ValueError:
                    phase = LifecyclePhase.ACTIVE

                distribution[phase.value] = distribution.get(phase.value, 0) + 1

                created = ensure_aware(m.created_at) or ref_now
                age_days = max(0.0, (ref_now - created).total_seconds() / 86400.0)

                trans = self.evaluate_transition_pure(
                    memory_id=m.id,
                    current_phase=phase,
                    base_confidence=m.confidence,
                    age_days=age_days,
                    use_count=m.use_count,
                    helpful_count=m.helpful_count,
                )

                if trans.from_phase != trans.to_phase:
                    if auto_apply:
                        trans = self.apply_transition(trans)
                        applied_count += 1
                    transitions.append(trans)

            summary = f"知命巡检：共审计 {len(items)} 条记忆，发现 {len(transitions)} 项状态转移建议，落库执行 {applied_count} 项"
            fenced = wrap_as_data(summary, item_id="zhiming-audit", score=1.0)

            return ZhimingResult(
                total_inspected=len(items),
                transitions=transitions,
                applied_count=applied_count,
                distribution=distribution,
                summary=summary,
                fenced_content=fenced,
            )

        if self.session is not None:
            return _run(self.session)
        with db.get_session() as s:
            return _run(s)
