"""钩沉（Gouchen · ADR-0082）：历史语境回溯与沉埋记忆检索恢复引擎。

出处：《易·系辞上》「探赜索隐，钩深致远」，宋代王应麟《困学纪闻》等考据治学常用“钩沉”指打捞湮没典籍。
职责：
1. 沉埋记忆度量推演（Sunken Score）；
2. 穿透归档与弱化状态的深水区打捞（Dredge Sunken Memories）；
3. 基于时间锚点（$T_{anchor}$）的历史语境快照无未来污染重建（Historical Context Reconstruction）；
4. 行级租户与属主安全隔离（宁漏不越权）；
5. 全面集成樊篱防注入安全围栏（wrap_as_data）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.time import ensure_aware, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.storage import db

DEFAULT_SUNKEN_THRESHOLD = 0.4


@dataclass
class GouchenItem:
    """钩沉单条沉埋记忆实体。"""

    memory_id: str
    content: str
    sunken_score: float
    decay_score: float
    age_days: float
    status: str
    lifecycle_status: str
    event_time: datetime | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "sunken_score": round(self.sunken_score, 4),
            "decay_score": round(self.decay_score, 4),
            "age_days": round(self.age_days, 1),
            "status": self.status,
            "lifecycle_status": self.lifecycle_status,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class HistoricalContext:
    """指定时态锚点的历史语境快照。"""

    anchor_time: datetime
    memories: list[GouchenItem] = field(default_factory=list)
    total_count: int = 0
    summary: str = ""
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "anchor_time": self.anchor_time.isoformat(),
            "memories": [m.to_dict(fenced=fenced) for m in self.memories],
            "total_count": self.total_count,
            "summary": self.summary,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class GouchenResult:
    """钩沉打捞检索结果。"""

    query: str
    total_dredged: int
    items: list[GouchenItem] = field(default_factory=list)
    summary: str = ""
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "query": self.query,
            "total_dredged": self.total_dredged,
            "items": [it.to_dict(fenced=fenced) for it in self.items],
            "summary": self.summary,
            "fenced_content": self.fenced_content if fenced else "",
        }


class GouchenEngine:
    """钩沉·历史语境回溯与沉埋记忆检索恢复引擎。"""

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

    def compute_sunken_score(self, decay_score: float, age_days: float) -> float:
        """纯函数：计算记忆沉没度打分（Sunken Score）。

        公式：
        SunkenScore = (1.0 - clamp(decay_score, 0, 1)) * (age_days / (age_days + 30.0))
        衰减越彻底、存续时间越久远，沉没度越高。
        """
        d = max(0.0, min(1.0, float(decay_score if decay_score is not None else 1.0)))
        a = max(0.0, float(age_days if age_days is not None else 0.0))
        if a <= 0.0:
            return 0.0
        return (1.0 - d) * (a / (a + 30.0))

    def dredge_sunken_memories(
        self,
        query: str = "",
        min_sunken_score: float = DEFAULT_SUNKEN_THRESHOLD,
        limit: int = 50,
        session: Session | None = None,
        now: datetime | None = None,
    ) -> GouchenResult:
        """从深水区穿透打捞沉埋、归档或弱化的历史记忆。"""
        q = (query or "").strip().lower()
        ref_now = now or utcnow()

        def _run(s: Session) -> GouchenResult:
            stmt = select(MemoryItem).where(MemoryItem.status != "deleted")
            # 行级 ACL 过滤
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

            candidates = s.exec(stmt.limit(limit * 3)).all()
            dredged_items: list[GouchenItem] = []

            for m in candidates:
                # 文本过滤
                c_text = m.content or ""
                if q and q not in c_text.lower():
                    continue

                created = ensure_aware(m.created_at) or ref_now
                age_days = max(0.0, (ref_now - created).total_seconds() / 86400.0)
                sunken_score = self.compute_sunken_score(
                    decay_score=m.decay_score,
                    age_days=age_days,
                )

                # 判定为沉埋：沉没度达标，或者本身即处于 archived / retired 状态
                is_sunken = (
                    sunken_score >= min_sunken_score
                    or m.status == "archived"
                    or (m.lifecycle_status or "").lower() in ("weakened", "retired")
                )
                if not is_sunken:
                    continue

                fenced = wrap_as_data(
                    c_text, item_id=f"gouchen-{m.id}", score=round(sunken_score, 3)
                )
                dredged_items.append(
                    GouchenItem(
                        memory_id=m.id,
                        content=c_text,
                        sunken_score=sunken_score,
                        decay_score=m.decay_score or 0.0,
                        age_days=age_days,
                        status=m.status,
                        lifecycle_status=m.lifecycle_status or "active",
                        event_time=ensure_aware(m.event_time),
                        user_id=m.user_id,
                        tenant_id=m.tenant_id,
                        fenced_content=fenced,
                    )
                )

            # 按沉没度从高到低排序
            dredged_items.sort(key=lambda x: x.sunken_score, reverse=True)
            dredged_items = dredged_items[:limit]

            summary = (
                f"钩沉打捞：针对查询【{query or '*'}】成功打捞 {len(dredged_items)} 条沉埋历史记忆"
            )
            fenced_summary = wrap_as_data(summary, item_id="gouchen-summary", score=1.0)

            return GouchenResult(
                query=query,
                total_dredged=len(dredged_items),
                items=dredged_items,
                summary=summary,
                fenced_content=fenced_summary,
            )

        active_session = session or self.session
        if active_session is not None:
            return _run(active_session)
        with db.get_session() as s:
            return _run(s)

    def reconstruct_historical_context(
        self,
        anchor_time: datetime,
        limit: int = 50,
        session: Session | None = None,
    ) -> HistoricalContext:
        """基于时序锚点 $T_{anchor}$ 重建当时生效的无未来污染历史语境。"""
        t_anchor = ensure_aware(anchor_time) or utcnow()

        def _run(s: Session) -> HistoricalContext:
            stmt = select(MemoryItem).where(MemoryItem.status != "deleted")
            # 行级 ACL 过滤
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

            candidates = s.exec(stmt.limit(limit * 3)).all()
            historical_items: list[GouchenItem] = []

            for m in candidates:
                created = ensure_aware(m.created_at)
                # 严禁未来信息泄漏：在 anchor_time 之后才创建的记录绝对不可见
                if created and created > t_anchor:
                    continue

                # 时效范围过滤：valid_from <= t_anchor < valid_to
                v_from = ensure_aware(m.valid_from)
                v_to = ensure_aware(m.valid_to)

                if v_from and v_from > t_anchor:
                    continue
                if v_to and v_to <= t_anchor:
                    continue

                age_days = max(0.0, (t_anchor - (created or t_anchor)).total_seconds() / 86400.0)
                sunken_score = self.compute_sunken_score(
                    decay_score=m.decay_score,
                    age_days=age_days,
                )

                c_text = m.content or ""
                fenced = wrap_as_data(c_text, item_id=f"history-{m.id}", score=1.0)
                historical_items.append(
                    GouchenItem(
                        memory_id=m.id,
                        content=c_text,
                        sunken_score=sunken_score,
                        decay_score=m.decay_score or 1.0,
                        age_days=age_days,
                        status=m.status,
                        lifecycle_status=m.lifecycle_status or "active",
                        event_time=ensure_aware(m.event_time),
                        user_id=m.user_id,
                        tenant_id=m.tenant_id,
                        fenced_content=fenced,
                    )
                )

            historical_items = historical_items[:limit]
            summary = f"历史语境重构：锚点【{t_anchor.isoformat()}】汇聚 {len(historical_items)} 条无污染记忆快照"
            aggregated_body = "\n".join(f"- {it.fenced_content}" for it in historical_items)
            full_content = f"{summary}\n{aggregated_body}" if aggregated_body else summary
            fenced_summary = wrap_as_data(full_content, item_id="history-context", score=1.0)

            return HistoricalContext(
                anchor_time=t_anchor,
                memories=historical_items,
                total_count=len(historical_items),
                summary=summary,
                fenced_content=fenced_summary,
            )

        active_session = session or self.session
        if active_session is not None:
            return _run(active_session)
        with db.get_session() as s:
            return _run(s)
