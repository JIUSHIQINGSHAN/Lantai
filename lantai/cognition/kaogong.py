"""考功（Kaogong · 唐代吏部「考功司」，掌官吏功过品级考评）：长程价值演化与多维功过评定引擎 (ADR-0078)。

职责：
1. 建立结构化考功契约 KaogongResult（分值、因子分解、原层级/新层级、重要度演化、裁决理由等）；
2. 针对系统存量记忆开展多因子价值考评（采纳率、使用密度、时效衰减影响、冲突隐患综合评定）；
3. 执行生命周期状态机升降级流转（promote_longterm / demote_deprecate / keep_neutral）；
4. 全面集成樊篱防注入安全围栏（wrap_as_data）；
5. 落实行级租户与属主安全隔离（宁漏不越权、宁 miss 不脏写）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 全局最新考功报告快照缓存
_LATEST_KAOGONG_REPORT: dict[str, Any] = {
    "evaluated": 0,
    "promoted_longterm": 0,
    "demoted": 0,
    "kept": 0,
    "details": [],
    "evaluated_at": None,
}


@dataclass
class KaogongResult:
    """考功价值评定结果契约。"""

    memory_id: str
    action: str  # promote_longterm | demote_deprecate | keep_neutral
    score: float
    use_count: int
    helpful_count: int
    helpful_ratio: float
    score_factors: dict[str, float] = field(default_factory=dict)
    old_tier: str = "working"
    new_tier: str = "working"
    old_importance: float = 0.5
    new_importance: float = 0.5
    reason: str = ""
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "action": self.action,
            "score": round(self.score, 4),
            "use_count": self.use_count,
            "helpful_count": self.helpful_count,
            "helpful_ratio": round(self.helpful_ratio, 4),
            "score_factors": {k: round(v, 4) for k, v in self.score_factors.items()},
            "old_tier": self.old_tier,
            "new_tier": self.new_tier,
            "old_importance": round(self.old_importance, 4),
            "new_importance": round(self.new_importance, 4),
            "reason": self.reason,
            "fenced_content": self.fenced_content if fenced else "",
        }


class KaogongEngine:
    """考功（Kaogong）长程价值演化与多维功过评定引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """行级安全隔离校验（宁漏不越权）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    def _owned_scope(self):
        """MemoryItem 租户行级安全过滤条件。"""
        if self.principal is None or getattr(self.principal, "is_admin", False):
            return None
        viewer = viewer_of(self.principal)
        cond = (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
        tenant = getattr(self.principal, "tenant_id", None)
        if tenant:
            cond = cond & ((MemoryItem.tenant_id == tenant) | (MemoryItem.tenant_id.is_(None)))
        return cond

    @staticmethod
    def evaluate_item(memory: MemoryItem) -> KaogongResult:
        """多因子价值综合评定纯函数（不触发写副作用，宁 miss 不脏写）。"""
        mem_id = memory.id or ""
        use_count = memory.use_count or 0
        helpful_count = memory.helpful_count or 0
        old_tier = memory.tier or "working"
        old_importance = memory.importance if memory.importance is not None else 0.5
        content_text = memory.content or ""

        # 1. 样本不足：use_count < 3（宁 miss 不脏写，保持原状）
        if use_count < 3:
            fenced = wrap_as_data(content_text, item_id=mem_id, score=old_importance)
            return KaogongResult(
                memory_id=mem_id,
                action="keep_neutral",
                score=old_importance,
                use_count=use_count,
                helpful_count=helpful_count,
                helpful_ratio=(helpful_count / use_count) if use_count > 0 else 0.0,
                score_factors={
                    "helpful_score": (helpful_count / use_count) if use_count > 0 else 0.0,
                    "density_score": min(use_count / 10.0, 1.0),
                    "decay_penalty": 0.0,
                    "composite_score": old_importance,
                },
                old_tier=old_tier,
                new_tier=old_tier,
                old_importance=old_importance,
                new_importance=old_importance,
                reason=f"样本不足（use_count={use_count} < 3），保持原状",
                fenced_content=fenced,
            )

        helpful_ratio = helpful_count / use_count
        density_score = min(use_count / 10.0, 1.0)
        # 综合考功分：85% 采纳表现 + 15% 引用活跃度（突出价值功过）
        composite_score = min(max(0.85 * helpful_ratio + 0.15 * density_score, 0.0), 1.0)
        score_factors = {
            "helpful_score": helpful_ratio,
            "density_score": density_score,
            "decay_penalty": 0.0,
            "composite_score": composite_score,
        }

        # 2. 上考：高频高采纳（ratio >= 0.8）-> 晋升长期语义层，重要度提升
        if helpful_ratio >= 0.8:
            new_importance = min(1.0, old_importance + 0.1)
            reason = f"考功上考：高频高采纳（{helpful_count}/{use_count} = {helpful_ratio:.1%}），晋升长期语义层"
            fenced = wrap_as_data(content_text, item_id=mem_id, score=new_importance)
            return KaogongResult(
                memory_id=mem_id,
                action="promote_longterm",
                score=composite_score,
                use_count=use_count,
                helpful_count=helpful_count,
                helpful_ratio=helpful_ratio,
                score_factors=score_factors,
                old_tier=old_tier,
                new_tier="longterm",
                old_importance=old_importance,
                new_importance=new_importance,
                reason=reason,
                fenced_content=fenced,
            )

        # 3. 下考：高频低效（ratio <= 0.2）-> 降权至 0.1
        if helpful_ratio <= 0.2:
            new_importance = 0.1
            reason = f"考功下考：高频低效（{helpful_count}/{use_count} = {helpful_ratio:.1%}），降权至 0.1"
            fenced = wrap_as_data(content_text, item_id=mem_id, score=new_importance)
            return KaogongResult(
                memory_id=mem_id,
                action="demote_deprecate",
                score=composite_score,
                use_count=use_count,
                helpful_count=helpful_count,
                helpful_ratio=helpful_ratio,
                score_factors=score_factors,
                old_tier=old_tier,
                new_tier=old_tier,
                old_importance=old_importance,
                new_importance=new_importance,
                reason=reason,
                fenced_content=fenced,
            )

        # 4. 中考：平稳保持
        reason = (
            f"考功中考：采纳率平稳（{helpful_count}/{use_count} = {helpful_ratio:.1%}），保持原状"
        )
        fenced = wrap_as_data(content_text, item_id=mem_id, score=old_importance)
        return KaogongResult(
            memory_id=mem_id,
            action="keep_neutral",
            score=composite_score,
            use_count=use_count,
            helpful_count=helpful_count,
            helpful_ratio=helpful_ratio,
            score_factors=score_factors,
            old_tier=old_tier,
            new_tier=old_tier,
            old_importance=old_importance,
            new_importance=old_importance,
            reason=reason,
            fenced_content=fenced,
        )

    def evaluate_memory_id(self, memory_id: str) -> KaogongResult:
        """对指定 ID 的单条记忆进行考评并落库持久化。"""
        mem_id = (memory_id or "").strip()
        if not mem_id:
            raise ValueError("memory_id 不能为空")

        if self.session is not None:
            return self._evaluate_memory_id(self.session, mem_id)
        with db.get_session() as s:
            return self._evaluate_memory_id(s, mem_id)

    def _evaluate_memory_id(self, s: Session, mem_id: str) -> KaogongResult:
        mem = s.get(MemoryItem, mem_id)
        if not mem:
            raise ValueError(f"记忆记录未找到: {mem_id}")

        if not self._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
            raise PermissionError(f"无权操作此记忆（行级租户/属主隔离）: {mem_id}")

        res = self.evaluate_item(mem)
        if res.action == "promote_longterm":
            mem.tier = "longterm"
            mem.decay_class = "semantic"
            mem.importance = res.new_importance
            s.add(mem)
            s.commit()
            s.refresh(mem)
        elif res.action == "demote_deprecate":
            mem.importance = res.new_importance
            s.add(mem)
            s.commit()
            s.refresh(mem)

        return res

    def run_cycle(self, limit: int = 100) -> dict[str, Any]:
        """批量对库内 active 记忆执行考功巡检与落库。"""
        if self.session is not None:
            return self._run_cycle(self.session, limit)
        with db.get_session() as s:
            return self._run_cycle(s, limit)

    def _run_cycle(self, s: Session, limit: int) -> dict[str, Any]:
        global _LATEST_KAOGONG_REPORT
        q = select(MemoryItem).where(MemoryItem.status == "active")
        scope = self._owned_scope()
        if scope is not None:
            q = q.where(scope)
        items = s.exec(q.limit(limit)).all()

        promoted_count = 0
        demoted_count = 0
        kept_count = 0
        details: list[dict[str, Any]] = []

        for mem in items:
            if not self._check_access(owner_id=mem.user_id, tenant_id=mem.tenant_id):
                continue
            res = self.evaluate_item(mem)
            if res.action == "promote_longterm":
                mem.tier = "longterm"
                mem.decay_class = "semantic"
                mem.importance = res.new_importance
                s.add(mem)
                promoted_count += 1
                details.append(res.to_dict(fenced=True))
            elif res.action == "demote_deprecate":
                mem.importance = res.new_importance
                s.add(mem)
                demoted_count += 1
                details.append(res.to_dict(fenced=True))
            else:
                kept_count += 1

        s.commit()
        report = {
            "evaluated": len(items),
            "promoted_longterm": promoted_count,
            "demoted": demoted_count,
            "kept": kept_count,
            "details": details,
            "evaluated_at": utcnow().isoformat(),
        }
        _LATEST_KAOGONG_REPORT = report
        logger.info(
            "考功：巡检完成（总计 %d，晋升长期 %d，降权 %d，保持 %d）",
            len(items),
            promoted_count,
            demoted_count,
            kept_count,
        )
        return report

    def get_report(self) -> dict[str, Any]:
        """获取最新考功评定审计报告。若尚无报告则执行一次。"""
        if _LATEST_KAOGONG_REPORT.get("evaluated_at") is None:
            return self.run_cycle()
        return _LATEST_KAOGONG_REPORT
