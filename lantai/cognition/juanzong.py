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

from lantai.core.acl import is_system_viewer, viewer_of
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
        return db.get_session()  # 票 67 弃用：仅存量调用兼容，公开方法已拆双轨

    def _check_access(self, dossier: Dossier) -> bool:
        """行级租户与属主 ACL 隔离校验（票 57：None 收敛 "default" 不再全放）。"""
        if getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal):
            return True
        viewer = viewer_of(self.principal)
        tenant_id = getattr(self.principal, "tenant_id", None)
        if tenant_id and dossier.tenant_id and dossier.tenant_id != tenant_id:
            return False
        return dossier.user_id is None or dossier.user_id == viewer

    def _check_memory_access(self, item: MemoryItem) -> bool:
        """校验记忆项的行级权限（票 57：None 收敛 "default"，他人原文不外泄）。"""
        if getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal):
            return True
        viewer = viewer_of(self.principal)
        tenant_id = getattr(self.principal, "tenant_id", None)
        if tenant_id and item.tenant_id and item.tenant_id != tenant_id:
            return False
        if item.user_id is not None and item.user_id != viewer:
            return False
        return not (
            getattr(self.principal, "allowed_lanes", None)
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
        if self.session is not None:
            return self._create_dossier(self.session, title, goal, milestones, memory_ids, metadata)
        with db.get_session() as s:  # 票 67：with 语义，返回即关（对齐 diben 样板）
            return self._create_dossier(s, title, goal, milestones, memory_ids, metadata)

    def _create_dossier(
        self,
        s: Session,
        title: str,
        goal: str,
        milestones: list[dict[str, Any]] | None,
        memory_ids: list[str] | None,
        metadata: dict[str, Any] | None,
    ) -> Dossier:
        """单轨实现（会话由调用方管理生命周期）。"""

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
        # 票 57：owner 一律 viewer_of 收敛（None→"default"），卷宗不落 NULL 属主行
        user_id = viewer_of(self.principal)
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
        if session is not None:
            return self._get_dossier(session, dossier_id)
        if self.session is not None:
            return self._get_dossier(self.session, dossier_id)
        with db.get_session() as s:  # 票 67
            return self._get_dossier(s, dossier_id)

    def _get_dossier(self, s: Session, dossier_id: str) -> Dossier | None:

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
        if session is not None:
            return self._list_dossiers(session, status, limit)
        if self.session is not None:
            return self._list_dossiers(self.session, status, limit)
        with db.get_session() as s:  # 票 67
            return self._list_dossiers(s, status, limit)

    def _list_dossiers(self, s: Session, status: str | None, limit: int) -> list[Dossier]:

        stmt = select(Dossier)
        if status:
            stmt = stmt.where(Dossier.status == status)

        tenant_id = getattr(self.principal, "tenant_id", None) if self.principal else None
        if tenant_id and not (self.principal and self.principal.is_admin):
            stmt = stmt.where((Dossier.tenant_id == tenant_id) | (Dossier.tenant_id.is_(None)))

        # 票 57：None 收敛 viewer_of="default"——此前无身份跳过本过滤，全库可见
        if not (getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal)):
            viewer = viewer_of(self.principal)
            stmt = stmt.where((Dossier.user_id == viewer) | (Dossier.user_id.is_(None)))

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
        if session is not None:
            return self._update_milestone(session, dossier_id, milestone_id, status, notes)
        if self.session is not None:
            return self._update_milestone(self.session, dossier_id, milestone_id, status, notes)
        with db.get_session() as s:  # 票 67
            return self._update_milestone(s, dossier_id, milestone_id, status, notes)

    def _update_milestone(
        self,
        s: Session,
        dossier_id: str,
        milestone_id: str,
        status: str,
        notes: str | None,
    ) -> Dossier | None:

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
        if session is not None:
            return self._bind_memories(session, dossier_id, memory_ids)
        if self.session is not None:
            return self._bind_memories(self.session, dossier_id, memory_ids)
        with db.get_session() as s:  # 票 67
            return self._bind_memories(s, dossier_id, memory_ids)

    def _bind_memories(self, s: Session, dossier_id: str, memory_ids: list[str]) -> bool:

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
        if session is not None:
            return self._unbind_memories(session, dossier_id, memory_ids)
        if self.session is not None:
            return self._unbind_memories(self.session, dossier_id, memory_ids)
        with db.get_session() as s:  # 票 67
            return self._unbind_memories(s, dossier_id, memory_ids)

    def _unbind_memories(self, s: Session, dossier_id: str, memory_ids: list[str]) -> bool:

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
        if session is not None:
            return self._close_dossier(session, dossier_id, summary, archived)
        if self.session is not None:
            return self._close_dossier(self.session, dossier_id, summary, archived)
        with db.get_session() as s:  # 票 67
            return self._close_dossier(s, dossier_id, summary, archived)

    def _close_dossier(
        self,
        s: Session,
        dossier_id: str,
        summary: str | None,
        archived: bool,
    ) -> Dossier | None:

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
        if session is not None:
            return self._focus_dossier_context(session, dossier_id, query, limit)
        if self.session is not None:
            return self._focus_dossier_context(self.session, dossier_id, query, limit)
        with db.get_session() as s:  # 票 67
            return self._focus_dossier_context(s, dossier_id, query, limit)

    def _focus_dossier_context(
        self,
        s: Session,
        dossier_id: str,
        query: str | None,
        limit: int,
    ) -> DossierFocusResult:

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
