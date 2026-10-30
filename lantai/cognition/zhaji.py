"""札记（Zhaji · ADR-0032 / ADR-0080）：工作区认知暂存便签深度演化、即时上下文熔断与双轨同步引擎。

出处：古代读书摘记要点之木简便签，《文史通义》论治学重札记积累。
职责：
1. 结构化多槽位解析与提炼（active_goals, key_findings, open_questions, notes）；
2. 平滑字符预算受控熔断与保整（按槽位优先级保留，严守「宁 miss 不脏写」）；
3. 真实数据库落库持久化与流转（SessionScratchpad）；
4. 行级租户与属主安全隔离（宁漏不越权）；
5. 全面集成樊篱防注入安全围栏（wrap_as_data）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session

from lantai.core.acl import viewer_of
from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import SessionScratchpad
from lantai.storage import db

DEFAULT_MAX_CHARS = 1000


@dataclass
class ZhajiItem:
    """札记结构化多槽位领域实体。"""

    session_id: str
    active_goals: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utcnow)
    user_id: str | None = None
    tenant_id: str | None = None
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "active_goals": list(self.active_goals),
            "key_findings": list(self.key_findings),
            "open_questions": list(self.open_questions),
            "notes": list(self.notes),
            "updated_at": self.updated_at.isoformat(),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "fenced_content": self.fenced_content if fenced else "",
        }


@dataclass
class ZhajiDigestResult:
    """札记紧凑提炼与预算熔断结果。"""

    session_id: str
    item: ZhajiItem
    total_slots: int
    compact_text: str
    truncated: bool = False
    fenced_content: str = ""

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_slots": self.total_slots,
            "compact_text": self.compact_text,
            "truncated": self.truncated,
            "item": self.item.to_dict(fenced=fenced),
            "fenced_content": self.fenced_content if fenced else "",
        }


class ZhajiEngine:
    """札记·工作区认知暂存与提纯引擎。"""

    def __init__(self, session: Session | None = None, principal=None):
        self.session = session
        self.principal = principal

    def _check_access(self, *, owner_id: str | None, tenant_id: str | None) -> bool:
        """行级租户与属主安全校验（宁漏不越权）。"""
        if self.principal is None:
            return True
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

    def distill_text(
        self,
        session_id: str,
        raw_text: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ZhajiItem:
        """从原始文本中结构化提取多槽位札记（支持标题、分段或自然列表）。"""
        sid = (session_id or "default").strip()
        lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]

        active_goals: list[str] = []
        key_findings: list[str] = []
        open_questions: list[str] = []
        notes: list[str] = []

        current_slot = "notes"

        # 槽位识别模式
        goal_pattern = re.compile(
            r"^(#+\s*)?(当前目标|目标|goals?|aims?)([:：\s]|$)", re.IGNORECASE
        )
        finding_pattern = re.compile(
            r"^(#+\s*)?(\[?关键发现\]?|发现|findings?|insights?)([:：\s]|$)", re.IGNORECASE
        )
        question_pattern = re.compile(
            r"^(#+\s*)?(【?待办与疑问】?|待办|疑问|questions?|todo|blockers?)([:：\s]|$)",
            re.IGNORECASE,
        )

        for line in lines:
            # 兼容 "|" 分隔的单行自然标注: "目标: xxx | 发现: yyy | 待办: zzz"
            if "|" in line:
                segments = [s.strip() for s in line.split("|") if s.strip()]
                for seg in segments:
                    if seg.startswith(("目标:", "目标：", "当前目标:", "当前目标：")):
                        val = seg.split(":", 1)[-1].split("：", 1)[-1].strip()
                        if val:
                            active_goals.append(val)
                    elif seg.startswith(("发现:", "发现：", "关键发现:", "关键发现：")):
                        val = seg.split(":", 1)[-1].split("：", 1)[-1].strip()
                        if val:
                            key_findings.append(val)
                    elif seg.startswith(
                        ("待办:", "待办：", "疑问:", "疑问：", "待办与疑问:", "待办与疑问：")
                    ):
                        val = seg.split(":", 1)[-1].split("：", 1)[-1].strip()
                        if val:
                            open_questions.append(val)
                    else:
                        notes.append(seg)
                continue

            # 检查分段标识（如 # 当前目标 或 目标: ...）
            if goal_pattern.search(line):
                current_slot = "goals"
                part = goal_pattern.sub("", line).strip()
                if part and part.startswith(("-", "*", ":", "：")):
                    part = part.lstrip("-*:： \t")
                if part:
                    active_goals.append(part)
                continue
            if finding_pattern.search(line):
                current_slot = "findings"
                part = finding_pattern.sub("", line).strip()
                if part and part.startswith(("-", "*", ":", "：")):
                    part = part.lstrip("-*:： \t")
                if part:
                    key_findings.append(part)
                continue
            if question_pattern.search(line):
                current_slot = "questions"
                part = question_pattern.sub("", line).strip()
                if part and part.startswith(("-", "*", ":", "：")):
                    part = part.lstrip("-*:： \t")
                if part:
                    open_questions.append(part)
                continue

            # 行内特定前缀
            if line.startswith("?"):
                open_questions.append(line.lstrip("? \t"))
                continue

            # 按当前激活槽位归集
            item_text = line.lstrip("-*• \t")
            if current_slot == "goals":
                active_goals.append(item_text)
            elif current_slot == "findings":
                key_findings.append(item_text)
            elif current_slot == "questions":
                open_questions.append(item_text)
            else:
                notes.append(item_text)

        item = ZhajiItem(
            session_id=sid,
            active_goals=active_goals,
            key_findings=key_findings,
            open_questions=open_questions,
            notes=notes,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return item

    def digest_item(self, item: ZhajiItem, max_chars: int = DEFAULT_MAX_CHARS) -> ZhajiDigestResult:
        """按优先级平滑紧凑提炼（goals > open_questions > key_findings > notes），受控预算熔断截断。"""
        truncated = False

        # 优先级编排定义: (前缀, 元素列表)
        priorities = [
            ("目标", item.active_goals),
            ("待办", item.open_questions),
            ("发现", item.key_findings),
            ("杂记", item.notes),
        ]

        current_len = 0
        accepted_parts: list[str] = []

        for label, elements in priorities:
            for elem in elements:
                clean_elem = (elem or "").strip()
                if not clean_elem:
                    continue
                snippet = f"{label}: {clean_elem}"
                needed_len = len(snippet) + (3 if accepted_parts else 0)  # " | " 分隔符
                if current_len + needed_len <= max_chars:
                    accepted_parts.append(snippet)
                    current_len += needed_len
                else:
                    truncated = True
                    break

        compact_text = " | ".join(accepted_parts)
        total_slots = (
            len(item.active_goals)
            + len(item.key_findings)
            + len(item.open_questions)
            + len(item.notes)
        )
        fenced = wrap_as_data(compact_text, item_id=f"zhaji-{item.session_id}", score=0.95)

        return ZhajiDigestResult(
            session_id=item.session_id,
            item=item,
            total_slots=total_slots,
            compact_text=compact_text,
            truncated=truncated,
            fenced_content=fenced,
        )

    def digest_session(
        self, session_id: str = "default", max_chars: int = DEFAULT_MAX_CHARS
    ) -> ZhajiDigestResult:
        """从 DB 读取持久化的便签并执行结构化提纯。"""
        sid = (session_id or "default").strip()

        def _read(s: Session) -> ZhajiDigestResult:
            row = s.get(SessionScratchpad, sid)
            if row is None:
                empty_item = ZhajiItem(session_id=sid)
                return self.digest_item(empty_item, max_chars=max_chars)

            if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                raise PermissionError(f"无权查看此会话札记便签（行级隔离）: {sid}")

            item = self.distill_text(
                session_id=sid,
                raw_text=row.content,
                user_id=row.user_id,
                tenant_id=row.tenant_id,
            )
            item.updated_at = row.updated_at
            return self.digest_item(item, max_chars=max_chars)

        if self.session is not None:
            return _read(self.session)
        with db.get_session() as s:
            return _read(s)

    def save_and_digest(
        self,
        session_id: str,
        raw_text: str,
        write: bool = True,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> ZhajiDigestResult:
        """解析并保存工作区便签（落库持久化）。"""
        sid = (session_id or "default").strip()
        u_id = getattr(self.principal, "user_id", None) if self.principal else None
        t_id = getattr(self.principal, "tenant_id", None) if self.principal else None

        item = self.distill_text(session_id=sid, raw_text=raw_text, user_id=u_id, tenant_id=t_id)
        digest = self.digest_item(item, max_chars=max_chars)

        if write:

            def _write(s: Session):
                row = s.get(SessionScratchpad, sid)
                if row:
                    if not self._check_access(owner_id=row.user_id, tenant_id=row.tenant_id):
                        raise PermissionError(f"无权修改此会话札记便签（行级隔离）: {sid}")
                    row.content = raw_text[:DEFAULT_MAX_CHARS]
                    row.updated_at = utcnow()
                    if u_id:
                        row.user_id = u_id
                    if t_id:
                        row.tenant_id = t_id
                    s.add(row)
                else:
                    new_row = SessionScratchpad(
                        session_id=sid,
                        content=raw_text[:DEFAULT_MAX_CHARS],
                        user_id=u_id or "default",
                        tenant_id=t_id,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    s.add(new_row)
                s.commit()

            if self.session is not None:
                _write(self.session)
            else:
                with db.get_session() as s:
                    _write(s)

        return digest
