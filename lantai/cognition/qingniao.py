"""青鸟（Qingniao · 李商隐《无题》「蓬山此去无多路，青鸟殷勤为探看」）：多智能体记忆信使与异步通信路由引擎。

为多智能体集群与认知联邦生态提供：
1. 青鸟记忆信函契约（QingniaoLetter）：封装跨智能体记忆信函，支持点对点（P2P）与广播（Broadcast）；
2. 异步派发回执契约（QingniaoDispatchResult）：投递入队与生命周期追踪；
3. 信函收箱与状态闭环（fetch_inbox / acknowledge_letter）：支持 ACK / REJECT 状态机转移；
4. 樊篱隔离防护（wrap_as_data）：记忆正文强制包裹 `<memory_data id="qingniao-...">` 防提示注入；
5. 租户隔离与行级 ACL 权限收窄：宁 miss 不越权，仅收信方/管理员有权确认信函。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session
from ulid import ULID

from lantai.core.time import utcnow
from lantai.llm.fence import wrap_as_data
from lantai.security.injection_guard import assert_no_prompt_injection

VALID_PRIORITIES = frozenset({"high", "normal", "low"})
VALID_STATUSES = frozenset({"pending", "delivered", "acknowledged", "rejected"})
VALID_ACTIONS = frozenset({"ack", "reject"})

# 进程内轻量信箱存储底座（以 letter_id 为键，隔离跨进程并发锁）
_QINGNIAO_MAILBOX: dict[str, dict[str, Any]] = {}


@dataclass
class QingniaoLetter:
    """青鸟记忆信函契约。"""

    letter_id: str
    sender_agent_id: str
    receiver_agent_id: str
    subject: str
    content: str
    priority: str
    status: str
    fenced_content: str
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    acknowledged_at: datetime | None = None

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        return {
            "letter_id": self.letter_id,
            "sender_agent_id": self.sender_agent_id,
            "receiver_agent_id": self.receiver_agent_id,
            "subject": self.subject,
            "content": self.content,
            "priority": self.priority,
            "status": self.status,
            "fenced_content": self.fenced_content if fenced else self.content,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "acknowledged_at": (self.acknowledged_at.isoformat() if self.acknowledged_at else None),
        }


@dataclass
class QingniaoDispatchResult:
    """青鸟信函派发回执契约。"""

    is_dispatched: bool
    letter_id: str
    delivered_count: int
    dispatched_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_dispatched": self.is_dispatched,
            "letter_id": self.letter_id,
            "delivered_count": self.delivered_count,
            "dispatched_at": (self.dispatched_at.isoformat() if self.dispatched_at else None),
        }


class QingniaoEngine:
    """青鸟·多智能体记忆信使与异步通信路由引擎。"""

    def __init__(self, session: Session | None = None, *, principal=None):
        self.session = session
        self.principal = principal

    def dispatch_letter(
        self,
        sender_agent_id: str,
        receiver_agent_id: str,
        subject: str,
        content: str,
        *,
        priority: str = "normal",
        namespace: str = "default",
    ) -> dict[str, Any]:
        """封装并派发记忆信函。

        1. 基础字段与优先级校验；
        2. 提示词注入攻击防御检测；
        3. 樊篱（wrap_as_data）防注入围栏包裹；
        4. 写入信箱并返回派发回执。
        """
        if not sender_agent_id or not sender_agent_id.strip():
            raise ValueError("sender_agent_id cannot be empty")
        if not receiver_agent_id or not receiver_agent_id.strip():
            raise ValueError("receiver_agent_id cannot be empty")
        if not subject or not subject.strip():
            raise ValueError("subject cannot be empty")
        if not content or not content.strip():
            raise ValueError("content cannot be empty")

        if priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{priority}', must be one of {sorted(VALID_PRIORITIES)}"
            )

        assert_no_prompt_injection(content)

        principal = self.principal
        user_id = (
            getattr(principal, "user_id", None) or "default" if principal is not None else "default"
        )
        tenant_id = getattr(principal, "tenant_id", None) if principal is not None else None

        letter_id = f"qnl_{ULID()}"
        fenced = wrap_as_data(content, item_id=f"qingniao-{letter_id}", score=1.0)
        now = utcnow()

        letter = QingniaoLetter(
            letter_id=letter_id,
            sender_agent_id=sender_agent_id.strip(),
            receiver_agent_id=receiver_agent_id.strip(),
            subject=subject.strip(),
            content=content.strip(),
            priority=priority,
            status="pending",
            fenced_content=fenced,
            user_id=user_id,
            tenant_id=tenant_id,
            created_at=now,
        )

        _QINGNIAO_MAILBOX[letter_id] = {
            **letter.to_dict(fenced=True),
            "namespace": namespace,
        }

        result = QingniaoDispatchResult(
            is_dispatched=True,
            letter_id=letter_id,
            delivered_count=1,
            dispatched_at=now,
        )
        return {
            "dispatch": result.to_dict(),
            "letter": letter.to_dict(fenced=True),
        }

    def fetch_inbox(
        self,
        receiver_agent_id: str,
        *,
        status: str | None = None,
        limit: int = 20,
        namespace: str = "default",
    ) -> list[dict[str, Any]]:
        """拉取指定智能体的待收/历史信件。

        遵循行级 ACL：跨租户不可见，仅目标智能体或广播信件可见。
        """
        if not receiver_agent_id or not receiver_agent_id.strip():
            raise ValueError("receiver_agent_id cannot be empty")

        principal = self.principal
        is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
        expected_tenant = getattr(principal, "tenant_id", None) if principal is not None else None

        matched: list[dict[str, Any]] = []
        for raw in _QINGNIAO_MAILBOX.values():
            if raw.get("namespace", "default") != namespace:
                continue

            # 租户隔离
            if not is_admin and expected_tenant is not None:
                if raw.get("tenant_id") != expected_tenant:
                    continue

            # 接收方匹配（支持单播与广播 *）
            target = raw.get("receiver_agent_id")
            if target != "*" and target != receiver_agent_id:
                continue

            # 状态过滤
            if status is not None and raw.get("status") != status:
                continue

            matched.append(raw)

        # 优先级排序：high > normal > low，然后按时间倒序
        priority_rank = {"high": 3, "normal": 2, "low": 1}
        matched.sort(
            key=lambda x: (
                priority_rank.get(x.get("priority", "normal"), 0),
                x.get("created_at") or "",
            ),
            reverse=True,
        )
        return matched[:limit]

    def acknowledge_letter(
        self,
        letter_id: str,
        receiver_agent_id: str,
        *,
        action: str = "ack",
        namespace: str = "default",
    ) -> dict[str, Any]:
        """收信确认或拒收处理。

        1. 检查信件存在性；
        2. 严格校验行级 ACL（只有目标接收方或超级管理员可执行确认）；
        3. 更新信件状态为 acknowledged 或 rejected。
        """
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action '{action}', must be one of {sorted(VALID_ACTIONS)}")

        raw = _QINGNIAO_MAILBOX.get(letter_id)
        if raw is None or raw.get("namespace", "default") != namespace:
            raise KeyError(f"Letter '{letter_id}' not found in namespace '{namespace}'")

        principal = self.principal
        is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
        expected_tenant = getattr(principal, "tenant_id", None) if principal is not None else None

        if not is_admin and expected_tenant is not None:
            if raw.get("tenant_id") != expected_tenant:
                raise PermissionError("Access denied: Tenant mismatch")

        # 接收者身份鉴权：只有信函的目标接收方（或广播信件接收者）或者 admin 才能签收
        target = raw.get("receiver_agent_id")
        if not is_admin and target != "*" and target != receiver_agent_id:
            raise PermissionError(
                f"Agent '{receiver_agent_id}' is not the recipient of letter '{letter_id}'"
            )

        new_status = "acknowledged" if action == "ack" else "rejected"
        raw["status"] = new_status
        raw["acknowledged_at"] = utcnow().isoformat()

        return {
            "letter_id": letter_id,
            "status": new_status,
            "receiver_agent_id": receiver_agent_id,
            "acknowledged_at": raw["acknowledged_at"],
        }
