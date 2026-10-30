"""底本（Diben）跨会话现场定本与认知状态传承引擎 (ADR-0072)。

出处：章学诚《校讎通义》「校讎之法，必本于底本。」底本即校勘纂辑所据之定本。
职责：
1. 提炼会话五段定本（SessionAnchor）：在做/下一步/工作区/决策/待办；
2. 意图延续与漂移偏转检测（analyze_drift）：判别当前输入是承接既有上下文还是意向漂移；
3. 全链路应用樊篱（wrap_as_data）防注入标记隔离；
4. 落实行级租户与属主安全隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jieba
from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import SessionCheckpoint
from lantai.storage import db


@dataclass
class SessionAnchor:
    """会话现场定本模型。"""

    session_id: str
    active_intent: str
    next_action: str
    working_context: str
    key_decisions: list[str] = field(default_factory=list)
    open_notes: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utcnow)
    user_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        text_summary = (
            f"在做: {self.active_intent} | "
            f"下一步: {self.next_action} | "
            f"工作区: {self.working_context} | "
            f"决策: {'; '.join(self.key_decisions)} | "
            f"待办: {'; '.join(self.open_notes)}"
        )
        fenced_c = (
            wrap_as_data(text_summary, item_id=f"anchor-{self.session_id}", score=0.95)
            if fenced
            else text_summary
        )
        return {
            "session_id": self.session_id,
            "active_intent": self.active_intent,
            "next_action": self.next_action,
            "working_context": self.working_context,
            "key_decisions": self.key_decisions,
            "open_notes": self.open_notes,
            "text_summary": text_summary,
            "fenced_content": fenced_c,
            "updated_at": self.updated_at.isoformat(),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


@dataclass
class DriftAnalysis:
    """意图漂移分析结果。"""

    query: str
    continuity_score: float
    is_continuation: bool
    drift_level: str  # none | slight | significant
    drift_reason: str
    fenced_reason: str
    analyzed_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "continuity_score": round(self.continuity_score, 4),
            "is_continuation": self.is_continuation,
            "drift_level": self.drift_level,
            "drift_reason": self.drift_reason,
            "fenced_reason": self.fenced_reason,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class DibenEngine:
    """底本（Diben）跨会话现场定本与认知状态传承引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """行级安全隔离校验（宁 miss 不脏写）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    def synthesize_anchor(
        self,
        session_id: str,
        blocks: dict[str, Any],
    ) -> dict[str, Any]:
        """将五段现场提炼并固化为当前会话底本。"""
        if self.session is not None:
            return self._synthesize_anchor(self.session, session_id, blocks)
        with db.get_session() as s:
            return self._synthesize_anchor(s, session_id, blocks)

    def _synthesize_anchor(
        self,
        s: Session,
        session_id: str,
        blocks: dict[str, Any],
    ) -> dict[str, Any]:
        viewer = viewer_of(self.principal) if self.principal else None
        current_tenant = getattr(self.principal, "tenant_id", None) if self.principal else None

        active_intent = str(blocks.get("cp_active_intent") or "").strip()
        next_action = str(blocks.get("cp_next_action") or "").strip()
        working_context = str(blocks.get("cp_current_work") or "").strip()

        decisions_raw = blocks.get("cp_key_decisions")
        if isinstance(decisions_raw, list):
            decisions = [str(d).strip() for d in decisions_raw if str(d).strip()]
        elif decisions_raw:
            decisions = [str(decisions_raw).strip()]
        else:
            decisions = []

        notes_raw = blocks.get("cp_open_notes")
        if isinstance(notes_raw, list):
            notes = [str(n).strip() for n in notes_raw if str(n).strip()]
        elif notes_raw:
            notes = [str(notes_raw).strip()]
        else:
            notes = []

        # 落库保存/更新 SessionCheckpoint
        block_items = [
            ("cp_active_intent", active_intent),
            ("cp_next_action", next_action),
            ("cp_current_work", working_context),
            ("cp_key_decisions", "; ".join(decisions)),
            ("cp_open_notes", "; ".join(notes)),
        ]

        t = utcnow()
        for k, v in block_items:
            if not v:
                continue
            cp = SessionCheckpoint(
                session_id=session_id,
                tenant_id=current_tenant,
                user_id=viewer,
                block_key=k,
                content=v,
                created_at=t,
            )
            s.add(cp)
        s.commit()

        anchor = SessionAnchor(
            session_id=session_id,
            active_intent=active_intent,
            next_action=next_action,
            working_context=working_context,
            key_decisions=decisions,
            open_notes=notes,
            updated_at=t,
            user_id=viewer,
            tenant_id=current_tenant,
        )
        return anchor.to_dict(fenced=True)

    def get_active_anchor(self, session_id: str | None = None) -> dict[str, Any] | None:
        """检索激活会话的最新底本定本。"""
        if self.session is not None:
            return self._get_active_anchor(self.session, session_id)
        with db.get_session() as s:
            return self._get_active_anchor(s, session_id)

    def _get_active_anchor(self, s: Session, session_id: str | None) -> dict[str, Any] | None:
        stmt = select(SessionCheckpoint)
        if session_id:
            stmt = stmt.where(SessionCheckpoint.session_id == session_id)
        stmt = stmt.order_by(SessionCheckpoint.created_at.desc())

        rows = s.exec(stmt).all()
        accessible_rows = [
            r for r in rows if self._check_access(owner_id=r.user_id, tenant_id=r.tenant_id)
        ]
        if not accessible_rows:
            return None

        target_sid = accessible_rows[0].session_id
        session_rows = [r for r in accessible_rows if r.session_id == target_sid]

        blocks: dict[str, str] = {}
        for r in session_rows:
            if r.block_key not in blocks:
                blocks[r.block_key] = r.content

        decisions = (
            [d.strip() for d in blocks.get("cp_key_decisions", "").split(";") if d.strip()]
            if blocks.get("cp_key_decisions")
            else []
        )
        notes = (
            [n.strip() for n in blocks.get("cp_open_notes", "").split(";") if n.strip()]
            if blocks.get("cp_open_notes")
            else []
        )

        anchor = SessionAnchor(
            session_id=target_sid,
            active_intent=blocks.get("cp_active_intent", ""),
            next_action=blocks.get("cp_next_action", ""),
            working_context=blocks.get("cp_current_work", ""),
            key_decisions=decisions,
            open_notes=notes,
            updated_at=session_rows[0].created_at,
            user_id=session_rows[0].user_id,
            tenant_id=session_rows[0].tenant_id,
        )
        return anchor.to_dict(fenced=True)

    def analyze_drift(
        self,
        current_query: str,
        anchor: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """分析当前用户输入与激活底本之间的意图延续性与漂移程度。"""
        if not anchor:
            anchor = self.get_active_anchor(session_id=session_id)

        if not anchor:
            fenced_reason = wrap_as_data(
                "无激活底本定本，默认作为新话题独立推进", item_id="no-anchor", score=1.0
            )
            return DriftAnalysis(
                query=current_query,
                continuity_score=0.0,
                is_continuation=False,
                drift_level="none",
                drift_reason="当前租户无激活底本定本，作为新话题处理",
                fenced_reason=fenced_reason,
            ).to_dict()

        anchor_text = f"{anchor.get('active_intent', '')} {anchor.get('next_action', '')} {anchor.get('working_context', '')}"
        q_words = [w.strip().lower() for w in jieba.cut(current_query or "") if len(w.strip()) > 1]
        a_words = [w.strip().lower() for w in jieba.cut(anchor_text or "") if len(w.strip()) > 1]

        q_set = set(q_words)
        a_set = set(a_words)

        overlap = q_set.intersection(a_set)
        score = len(overlap) / max(len(q_set), 1)

        if score >= 0.35:
            drift_level = "none"
            is_cont = True
            reason = f"高契合度延续既有会话意图（关键词匹配: {', '.join(list(overlap)[:3])}）"
        elif score >= 0.15:
            drift_level = "slight"
            is_cont = True
            reason = f"轻度延伸相关任务（关键词匹配: {', '.join(list(overlap)[:2])}）"
        else:
            drift_level = "significant"
            is_cont = False
            reason = "语义实词重合极低，判定为明显话题偏离或转向全新意图"

        fenced_reason = wrap_as_data(
            reason, item_id=f"drift-{anchor.get('session_id')}", score=score
        )

        return DriftAnalysis(
            query=current_query,
            continuity_score=score,
            is_continuation=is_cont,
            drift_level=drift_level,
            drift_reason=reason,
            fenced_reason=fenced_reason,
        ).to_dict()
