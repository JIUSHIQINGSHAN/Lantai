"""起居注（Qijuzhu）时序记忆脉络与每日全息纪事引擎 (ADR-0066)。

典籍出处：《汉书·艺文志》「古之人君，左史记言，右史记事」，逐日详录言行得失、政事变迁，为国史实录之源。
解决痛点：
1. 突破既有静态粗粒度每日盘点（digest_worker.py）仅离线生成纯指标 Markdown 的局限；
2. 构建多源认知变迁事件流（Chronicle Stream: 记忆摄入/规则确立/卷宗里程碑/提案演化）；
3. 提供全息日度简报与滑动时间轴脉络，正文由樊篱（wrap_as_data）防注入围栏严密包裹；
4. 严格落实行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlmodel import Session, select

from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import Dossier, MemoryItem, MemoryProposal
from lantai.storage import db


@dataclass
class ChronicleEvent:
    """起居注单项认知事件条目。"""

    event_type: str  # memory_ingested | rule_established | dossier_milestone | proposal_applied
    timestamp: datetime
    title: str
    detail: str
    ref_id: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "detail": self.detail,
            "ref_id": self.ref_id,
            "meta": self.meta,
        }


class QijuzhuEngine:
    """起居注·时序记忆脉络与每日全息纪事聚合引擎。"""

    def __init__(
        self,
        session: Session | None = None,
        principal: Principal | None = None,
    ):
        self.session = session
        self.principal = principal

    def _get_session(self) -> Session:
        if self.session is not None:
            return self.session
        return db.get_session()

    def _check_memory_access(self, item: MemoryItem) -> bool:
        """MemoryItem 租户与 ACL 权限校验（宁 miss 不脏写）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        if self.principal.tenant_id and item.tenant_id and item.tenant_id != self.principal.tenant_id:
            return False
        user_id = self.principal.user_id
        if user_id and item.user_id and item.user_id != user_id:
            return False
        if self.principal.allowed_lanes is not None:
            return item.lane in self.principal.allowed_lanes
        return True

    def _check_dossier_access(self, dossier: Dossier) -> bool:
        """Dossier 租户与属主校验。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        if self.principal.tenant_id and dossier.tenant_id and dossier.tenant_id != self.principal.tenant_id:
            return False
        user_id = self.principal.user_id
        if user_id and dossier.user_id:
            return dossier.user_id == user_id
        return True

    def _check_proposal_access(self, proposal: MemoryProposal) -> bool:
        """MemoryProposal 租户与属主校验。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True
        if self.principal.tenant_id and proposal.tenant_id and proposal.tenant_id != self.principal.tenant_id:
            return False
        user_id = self.principal.user_id
        if user_id and proposal.user_id:
            return proposal.user_id == user_id
        return True

    def _fetch_events_between(
        self,
        session: Session,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ChronicleEvent]:
        """抓取指定时间窗口内的多源认知演进事件。"""
        events: list[ChronicleEvent] = []

        # 1. 记忆条目事件（含普通记忆与程序铁律）
        mem_q = select(MemoryItem).where(
            MemoryItem.created_at >= start_time,
            MemoryItem.created_at < end_time,
        )
        memories = session.exec(mem_q).all()
        for m in memories:
            if not self._check_memory_access(m):
                continue
            is_rule = m.lane == "rule" or m.decay_class == "procedural"
            ev_type = "rule_established" if is_rule else "memory_ingested"
            title = f"确立程序铁律: {m.content[:24]}..." if is_rule else f"摄入事实: {m.content[:24]}..."
            safe_detail = wrap_as_data(m.content, item_id=m.id)
            events.append(
                ChronicleEvent(
                    event_type=ev_type,
                    timestamp=m.created_at,
                    title=title,
                    detail=safe_detail,
                    ref_id=m.id,
                    meta={"lane": m.lane, "decay_class": m.decay_class, "importance": m.importance},
                )
            )

        # 2. 卷宗推进事件（Dossier）
        try:
            dos_q = select(Dossier).where(
                Dossier.created_at >= start_time,
                Dossier.created_at < end_time,
            )
            dossiers = session.exec(dos_q).all()
            for d in dossiers:
                if not self._check_dossier_access(d):
                    continue
                # 卷宗主事件
                milestones_done = [ms for ms in (d.milestones or []) if ms.get("status") == "done"]
                ms_text = f"完成 {len(milestones_done)} 个里程碑" if milestones_done else "规划中"
                dos_detail = f"卷宗目标: {d.goal} | 进展: {ms_text}"
                safe_dos_detail = wrap_as_data(dos_detail, item_id=d.id)
                events.append(
                    ChronicleEvent(
                        event_type="dossier_milestone",
                        timestamp=d.created_at,
                        title=f"卷宗情境: {d.title}",
                        detail=safe_dos_detail,
                        ref_id=d.id,
                        meta={"status": d.status, "milestone_count": len(d.milestones or [])},
                    )
                )
        except Exception:
            pass

        # 3. 提案应用事件（MemoryProposal）
        try:
            prop_q = select(MemoryProposal).where(
                MemoryProposal.created_at >= start_time,
                MemoryProposal.created_at < end_time,
            )
            proposals = session.exec(prop_q).all()
            for p in proposals:
                if not self._check_proposal_access(p):
                    continue
                safe_prop_detail = wrap_as_data(p.reason or "知识演化提案", item_id=p.id)
                events.append(
                    ChronicleEvent(
                        event_type="proposal_applied" if p.status == "applied" else "proposal_created",
                        timestamp=p.created_at,
                        title=f"知识提案 [{p.proposal_type}]: {p.reason[:24]}...",
                        detail=safe_prop_detail,
                        ref_id=p.id,
                        meta={"proposal_type": p.proposal_type, "status": p.status},
                    )
                )
        except Exception:
            pass

        return events

    def get_daily_chronicle(
        self,
        date: str | date | None = None,
        detail_level: str = "summary",
    ) -> dict[str, Any]:
        """获取指定日期的全息起居注纪事。

        Args:
            date: 日期字符串 (YYYY-MM-DD) 或 date 对象，默认今日。
            detail_level: "summary" 或 "full"。
        """
        session = self._get_session()
        target_date: date
        if date is None:
            target_date = utcnow().date()
        elif isinstance(date, str):
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        else:
            target_date = date

        start_time = datetime.combine(target_date, time.min, tzinfo=UTC)
        end_time = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=UTC)

        events = self._fetch_events_between(session, start_time, end_time)
        # 按时间正序
        events.sort(key=lambda x: x.timestamp)

        # 统计度量指标
        total_events = len(events)
        new_memories = sum(1 for e in events if e.event_type in ("memory_ingested", "rule_established"))
        rules_established = sum(1 for e in events if e.event_type == "rule_established")
        dossier_events = sum(1 for e in events if e.event_type == "dossier_milestone")
        proposals_applied = sum(1 for e in events if e.event_type == "proposal_applied")

        summary_parts = [
            f"{target_date.isoformat()} 全息纪事共记录 {total_events} 项变迁",
            f"新摄入记忆 {new_memories} 条",
        ]
        if rules_established > 0:
            summary_parts.append(f"确立操作铁律 {rules_established} 条")
        if dossier_events > 0:
            summary_parts.append(f"卷宗情境流转 {dossier_events} 起")
        if proposals_applied > 0:
            summary_parts.append(f"演化生效提案 {proposals_applied} 项")

        return {
            "date": target_date.isoformat(),
            "summary": " · ".join(summary_parts),
            "metrics": {
                "total_events": total_events,
                "new_memories": new_memories,
                "rules_established": rules_established,
                "dossier_events": dossier_events,
                "proposals_applied": proposals_applied,
            },
            "chronicle_events": [e.to_dict() for e in events],
            "detail_level": detail_level,
            "generated_at": utcnow().isoformat(),
        }

    def get_timeline(
        self,
        days: int = 3,
        limit: int = 50,
    ) -> dict[str, Any]:
        """获取滑动时间窗口内的时序记忆脉络事件流。

        Args:
            days: 回溯滑动天数，默认 3 天。
            limit: 最大返回事件数。
        """
        session = self._get_session()
        now = utcnow()
        start_time = now - timedelta(days=days)

        events = self._fetch_events_between(session, start_time, now + timedelta(seconds=1))
        # 时间倒序排布（最近事件置顶）
        events.sort(key=lambda x: x.timestamp, reverse=True)
        truncated_events = events[:limit]

        return {
            "days": days,
            "total_matched": len(events),
            "events": [e.to_dict() for e in truncated_events],
            "window_start": start_time.isoformat(),
            "window_end": now.isoformat(),
        }
