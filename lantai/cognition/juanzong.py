"""卷宗（Juanzong）长程复杂任务情境聚合与目标上下文锚定引擎 (ADR-0063)

典籍出处：唐代白居易《新乐府》「卷宗堆案墨翻盆」，官府按案归档、统摄全貌之档卷。
解决痛点：
1. 突破既有 MemoryScene 纯无状态余弦聚类的局限，维护有状态的长程任务案卷（Dossier）；
2. 任务战略目标（Goal）与多阶段里程碑（Milestones）状态机追踪；
3. 动态情境记忆聚焦检索（Dossier Context Focusing），优先召回关联事实与规则，防止注意力涣散；
4. 严格行级租户安全隔离与「宁 miss 不脏写」纪律。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from lantai.core.auth import Principal
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import Dossier, MemoryItem
from lantai.storage import db


@dataclass
class DossierMilestone:
    """卷宗里程碑状态项。"""

    id: str
    title: str
    status: str = "pending"  # pending | in_progress | done
    notes: str = ""


@dataclass
class DossierFocusResult:
    """卷宗情境聚焦检索结果。"""

    dossier_id: str
    dossier_title: str
    dossier_goal: str
    focused_memories: list[dict[str, Any]]
    milestones: list[dict[str, Any]]
    total_bound_memories: int
    focused_at: datetime = field(default_factory=utcnow)


class JuanzongEngine:
    """卷宗·长程复杂任务情境聚合引擎。"""

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

    def _check_access(self, dossier: Dossier) -> bool:
        """行级租户与属主 ACL 隔离校验。"""
        if self.principal is None:
            return True
        if self.principal.is_admin:
            return True
        tenant_id = getattr(self.principal, "tenant_id", None)
        if tenant_id and dossier.tenant_id and dossier.tenant_id != tenant_id:
            return False
        return not (
            dossier.user_id and self.principal.user_id and dossier.user_id != self.principal.user_id
        )

    def _check_memory_access(self, item: MemoryItem) -> bool:
        """校验记忆项的行级权限。"""
        if self.principal is None:
            return True
        if self.principal.is_admin:
            return True
        tenant_id = getattr(self.principal, "tenant_id", None)
        if tenant_id and item.tenant_id and item.tenant_id != tenant_id:
            return False
        if item.user_id and self.principal.user_id and item.user_id != self.principal.user_id:
            return False
        return not (
            self.principal.allowed_lanes
            and item.lane
            and item.lane not in self.principal.allowed_lanes
        )

    def create_dossier(
        self,
        title: str,
        goal: str = "",
        milestones: list[dict[str, Any]] | None = None,
        memory_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Dossier:
        """创建长程任务情境卷宗。"""
        s = self._get_session()
        dos_id = new_id("dos")

        # 规整里程碑列表
        normalized_milestones: list[dict[str, Any]] = []
        if milestones:
            for idx, m in enumerate(milestones):
                m_id = m.get("id") or f"ms-{idx + 1}"
                normalized_milestones.append(
                    {
                        "id": str(m_id),
                        "title": str(m.get("title", "")),
                        "status": str(m.get("status", "pending")),
                        "notes": str(m.get("notes", "")),
                    }
                )

        # 规整绑定的记忆项 ID
        valid_memory_ids: list[str] = []
        if memory_ids:
            for mid in memory_ids:
                if isinstance(mid, str) and mid.strip() and mid.strip() not in valid_memory_ids:
                    valid_memory_ids.append(mid.strip())

        meta = dict(metadata or {})
        user_id = self.principal.user_id if self.principal else None
        tenant_id = getattr(self.principal, "tenant_id", None) if self.principal else None

        dossier = Dossier(
            id=dos_id,
            tenant_id=tenant_id,
            user_id=user_id,
            title=title,
            goal=goal,
            status="active",
            milestones=normalized_milestones,
            memory_ids=valid_memory_ids,
            metadata_json=meta,
        )
        s.add(dossier)
        s.commit()
        s.refresh(dossier)
        return dossier

    def get_dossier(self, dossier_id: str, session: Session | None = None) -> Dossier | None:
        """获取卷宗详情（受租户行级安全保护）。"""
        s = session or self._get_session()
        dossier = s.get(Dossier, dossier_id)
        if not dossier or not self._check_access(dossier):
            return None
        return dossier

    def list_dossiers(
        self,
        status: str | None = None,
        limit: int = 50,
        session: Session | None = None,
    ) -> list[Dossier]:
        """查询符合权限的卷宗清单。"""
        s = session or self._get_session()
        stmt = select(Dossier)
        if status:
            stmt = stmt.where(Dossier.status == status)

        tenant_id = getattr(self.principal, "tenant_id", None) if self.principal else None
        if tenant_id and not (self.principal and self.principal.is_admin):
            stmt = stmt.where((Dossier.tenant_id == tenant_id) | (Dossier.tenant_id.is_(None)))

        if self.principal is not None and not self.principal.is_admin and self.principal.user_id:
            stmt = stmt.where(
                (Dossier.user_id == self.principal.user_id) | (Dossier.user_id.is_(None))
            )

        stmt = stmt.order_by(Dossier.updated_at.desc()).limit(limit)
        items = s.exec(stmt).all()
        return [it for it in items if self._check_access(it)]

    def update_milestone(
        self,
        dossier_id: str,
        milestone_id: str,
        status: str,
        notes: str | None = None,
        session: Session | None = None,
    ) -> Dossier | None:
        """流转更新卷宗内的任务里程碑。"""
        s = session or self._get_session()
        dossier = self.get_dossier(dossier_id, session=s)
        if not dossier:
            return None

        milestones = list(dossier.milestones or [])
        found = False
        for m in milestones:
            if m.get("id") == milestone_id:
                m["status"] = status
                if notes is not None:
                    m["notes"] = notes
                found = True
                break

        if not found:
            milestones.append(
                {
                    "id": milestone_id,
                    "title": milestone_id,
                    "status": status,
                    "notes": notes or "",
                }
            )

        dossier.milestones = [dict(m) for m in milestones]
        flag_modified(dossier, "milestones")
        dossier.updated_at = utcnow()
        s.add(dossier)
        s.commit()
        s.refresh(dossier)
        return dossier

    def bind_memories(
        self,
        dossier_id: str,
        memory_ids: list[str],
        session: Session | None = None,
    ) -> bool:
        """向卷宗幂等且保序地绑定记忆项。"""
        s = session or self._get_session()
        dossier = self.get_dossier(dossier_id, session=s)
        if not dossier:
            return False

        current = list(dossier.memory_ids or [])
        existing = set(current)
        new_added = False
        for mid in memory_ids:
            if isinstance(mid, str) and mid.strip() and mid.strip() not in existing:
                existing.add(mid.strip())
                current.append(mid.strip())
                new_added = True

        if new_added:
            dossier.memory_ids = current
            flag_modified(dossier, "memory_ids")
            dossier.updated_at = utcnow()
            s.add(dossier)
            s.commit()
            s.refresh(dossier)
        return True

    def unbind_memories(
        self,
        dossier_id: str,
        memory_ids: list[str],
        session: Session | None = None,
    ) -> bool:
        """从卷宗解绑记忆项。"""
        s = session or self._get_session()
        dossier = self.get_dossier(dossier_id, session=s)
        if not dossier:
            return False

        to_remove = set(memory_ids)
        current = list(dossier.memory_ids or [])
        filtered = [mid for mid in current if mid not in to_remove]

        if len(filtered) != len(current):
            dossier.memory_ids = filtered
            flag_modified(dossier, "memory_ids")
            dossier.updated_at = utcnow()
            s.add(dossier)
            s.commit()
            s.refresh(dossier)
        return True

    def close_dossier(
        self,
        dossier_id: str,
        summary: str | None = None,
        archived: bool = False,
        session: Session | None = None,
    ) -> Dossier | None:
        """办结或归档卷宗。"""
        s = session or self._get_session()
        dossier = self.get_dossier(dossier_id, session=s)
        if not dossier:
            return None

        dossier.status = "archived" if archived else "completed"
        if summary:
            meta = dict(dossier.metadata_json or {})
            meta["closing_summary"] = summary
            dossier.metadata_json = meta
            flag_modified(dossier, "metadata_json")

        dossier.updated_at = utcnow()
        s.add(dossier)
        s.commit()
        s.refresh(dossier)
        return dossier

    def focus_dossier_context(
        self,
        dossier_id: str,
        query: str | None = None,
        limit: int = 20,
        session: Session | None = None,
    ) -> DossierFocusResult:
        """针对指定卷宗执行情境记忆聚焦检索（Dossier Context Focusing）。"""
        s = session or self._get_session()
        dossier = self.get_dossier(dossier_id, session=s)
        if not dossier:
            return DossierFocusResult(
                dossier_id=dossier_id,
                dossier_title="[未知或无权访问]",
                dossier_goal="",
                focused_memories=[],
                milestones=[],
                total_bound_memories=0,
            )

        bound_ids = list(dossier.memory_ids or [])
        focused_memories: list[dict[str, Any]] = []

        if bound_ids:
            # 批量获取绑定的记忆并做权限检查（避免 N+1 查询）
            items = s.exec(select(MemoryItem).where(MemoryItem.id.in_(bound_ids))).all()
            item_map = {item.id: item for item in items}

            for mid in bound_ids:
                item = item_map.get(mid)
                if item and item.status == "active" and self._check_memory_access(item):
                    focused_memories.append(
                        {
                            "id": item.id,
                            "content": item.content,
                            "role": item.role,
                            "confidence": item.confidence,
                            "event_time": str(item.event_time) if item.event_time else None,
                        }
                    )

        # 若提供了 query，结合中文分词或子串打分
        if query and focused_memories:
            import re

            q_clean = query.strip().lower()
            # 提取单词、单字（对中文，提取 2-gram 和整词以保证子串匹配敏感度）
            q_terms = [t for t in re.split(r"[\s,，。；;]+", q_clean) if t]
            bigrams = []
            for term in q_terms:
                if len(term) >= 2:
                    for i in range(len(term) - 1):
                        bigrams.append(term[i : i + 2])

            def _rank(m: dict[str, Any]) -> float:
                content = str(m.get("content", "")).lower()
                # 1. 整词匹配
                overlap = sum(2.0 for t in q_terms if t in content) if q_terms else 0.0
                # 2. 2-gram 中文子串命中
                ngram_match = sum(1.0 for bg in bigrams if bg in content) if bigrams else 0.0
                conf = float(m.get("confidence") or 0.5)
                return overlap + ngram_match + conf

            focused_memories.sort(key=_rank, reverse=True)

        return DossierFocusResult(
            dossier_id=dossier.id,
            dossier_title=dossier.title,
            dossier_goal=dossier.goal,
            focused_memories=focused_memories[:limit],
            milestones=list(dossier.milestones or []),
            total_bound_memories=len(bound_ids),
        )
