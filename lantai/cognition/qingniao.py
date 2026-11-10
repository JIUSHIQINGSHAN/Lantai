"""青鸟（Qingniao · 李商隐《无题》「蓬山此去无多路，青鸟殷勤为探看」）：多智能体记忆信使与异步通信路由引擎。

为多智能体集群与认知联邦生态提供：
1. 青鸟记忆信函契约（QingniaoLetter）：封装跨智能体记忆信函，支持点对点（P2P）与广播（Broadcast）；
2. 异步派发回执契约（QingniaoDispatchResult）：投递入队与生命周期追踪；
3. 信函收箱与状态闭环（fetch_inbox / acknowledge_letter）：pending → acknowledged | rejected
   两态直达状态机（票 63 决策点 A：delivered 中间态裁掉——全仓零写点零消费），终态不可翻转；
4. message_key 幂等投递（票 63 决策点 B）：同 key 同 namespace 重复投递返回原信不新建；
5. 樊篱隔离防护（wrap_as_data）：记忆正文强制包裹 `<memory_data id="qingniao-...">` 防提示注入，
   subject 与 content 双闸过獬豸（票 63 半边 4）；
6. 租户隔离与行级 ACL 权限收窄（票 63 半边 1+2）：租户过滤无条件建立（None 收敛 "default"，
   票 57 家族范式）、单播信函仅信函属主可见/可签收（广播豁免）、admin 全表——宁 miss 不越权。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session
from ulid import ULID

from lantai.core.acl import viewer_of
from lantai.core.time import iso_utc, utcnow
from lantai.llm.fence import wrap_as_data
from lantai.security.injection_guard import assert_no_prompt_injection

VALID_PRIORITIES = frozenset({"high", "normal", "low"})
# 票 63 决策点 A：delivered 中间态裁掉（全仓零写点零消费——fetch 只读不改
# 状态，给拉取加副作用破坏「拉取只读」契约且破坏既有 smoke）。两态直达：
# pending → acknowledged | rejected，生命周期闭环由 ack/reject 完成。
VALID_STATUSES = frozenset({"pending", "acknowledged", "rejected"})
VALID_ACTIONS = frozenset({"ack", "reject"})

# 进程内轻量信箱存储底座（以 letter_id 为键，隔离跨进程并发锁）
_QINGNIAO_MAILBOX: dict[str, dict[str, Any]] = {}

# 票 63 决策点 B：message_key → letter_id 幂等索引（同 key 同 namespace 重复
# 投递返回原信不新建）。随信箱同生共死（进程内，无持久化负担）。
_QINGNIAO_MESSAGE_KEYS: dict[tuple[str, str], str] = {}


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
            "created_at": iso_utc(self.created_at) or None,
            "acknowledged_at": iso_utc(self.acknowledged_at) or None,
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
            "dispatched_at": iso_utc(self.dispatched_at) or None,
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
        message_key: str = "",
    ) -> dict[str, Any]:
        """封装并派发记忆信函。

        1. 基础字段与优先级校验；
        2. 提示词注入攻击防御检测（subject 与 content 双闸，票 63）；
        3. 樊篱（wrap_as_data）防注入围栏包裹；
        4. message_key 幂等去重（同 key 同 namespace 返回原信，票 63）；
        5. 写入信箱并返回派发回执。
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

        # 票 63 半边 4：subject 与 content 同过獬豸——subject 是用户可控自由
        # 文本，经信使跨会话投递进 LLM 可见面（context.py 青鸟切面），注入
        # 载荷借道 subject 与借道 content 同危害，整条拒（宁 miss 不脏写）。
        assert_no_prompt_injection(subject)
        assert_no_prompt_injection(content)

        principal = self.principal

        user_id = viewer_of(principal)
        # 票 63 半边 1（写侧）：tenant None 收敛 "default"（票 57 家族范式）——
        # 读侧同收敛后过滤无条件建立，NULL 半边（过滤整段跳过）消灭。
        tenant_id = (
            getattr(principal, "tenant_id", None) or "default"
            if principal is not None
            else "default"
        )

        # 票 63 决策点 B：message_key 幂等——显式参数（非 sender+receiver+
        # subject 隐式派生：不同业务方同主题投递会被误合并，脏写）。同 key
        # 同 namespace 同租户同属主重复投递返回原信 + deduplicated 如实标注。
        # 索引键含 (tenant, user) 维度（自审 Spec 轴实抓：缺维度时跨租户/跨
        # user 同 key 投递会返回他人原信 dict 含正文——dispatch 返回路径绕过
        # tenant 闸与属主闸，越权泄漏）。
        message_key = message_key.strip() if isinstance(message_key, str) else ""
        if message_key:
            dedup_idx = (namespace, tenant_id, user_id, message_key)
            existing_id = _QINGNIAO_MESSAGE_KEYS.get(dedup_idx)
            if existing_id is not None and existing_id in _QINGNIAO_MAILBOX:
                existing = _QINGNIAO_MAILBOX[existing_id]
                return {
                    "dispatch": {
                        "is_dispatched": True,
                        "letter_id": existing_id,
                        "delivered_count": 0,
                        "dispatched_at": existing.get("created_at"),
                        "deduplicated": True,
                    },
                    "letter": existing,
                }

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
        if message_key:
            _QINGNIAO_MESSAGE_KEYS[(namespace, tenant_id, user_id, message_key)] = letter_id

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

        行级 ACL（票 63 半边 1+2）：租户过滤无条件建立（None 收敛 "default"，
        票 57 家族范式）；单播信函仅信函属主（letter.user_id==viewer）可见
        （user_id 写而不读病灶——同租户跨 user 信箱互通），广播信函（"*"）
        豁免属主闸（租户闸兜底，ADR-0075 租户内广播语义保留）。admin 全表。
        属主不匹配**跳过隐藏**而非抛错——若他人跨 user 投递的信函混进本人
        信箱，抛错会让本人读不了自己的信箱，且错误信息侧信道暴露信箱构成
        （宁 miss 不越权）。
        """
        if not receiver_agent_id or not receiver_agent_id.strip():
            raise ValueError("receiver_agent_id cannot be empty")

        principal = self.principal
        is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
        # 票 63 半边 1（读侧）：None 收敛 "default"，过滤无条件建立
        expected_tenant = (
            (getattr(principal, "tenant_id", None) or "default")
            if principal is not None
            else "default"
        )

        viewer = viewer_of(principal)

        matched: list[dict[str, Any]] = []
        for raw in _QINGNIAO_MAILBOX.values():
            if raw.get("namespace", "default") != namespace:
                continue

            # 租户隔离（无条件，NULL 半边已收敛消灭）
            if not is_admin and raw.get("tenant_id") != expected_tenant:
                continue

            # 票 63 半边 2：信函属主闸（单播信函仅属主可见；广播豁免）
            if (
                not is_admin
                and raw.get("receiver_agent_id") != "*"
                and raw.get("user_id") != viewer
            ):
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
        2. 严格校验行级 ACL（租户闸无条件建立 + 单播信函仅信函属主或 admin
           可签收，票 63 半边 1+2；广播信函豁免属主闸）；
        3. 状态机转移（票 63 半边 3）：pending → acknowledged | rejected 两态
           直达；终态（acknowledged/rejected）不可翻转——同向幂等重入返回
           现态（already_* 如实标注，不覆盖 acknowledged_at），异向翻转
           ValueError 拒绝。
        """
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid action '{action}', must be one of {sorted(VALID_ACTIONS)}")

        raw = _QINGNIAO_MAILBOX.get(letter_id)
        if raw is None or raw.get("namespace", "default") != namespace:
            raise KeyError(f"Letter '{letter_id}' not found in namespace '{namespace}'")

        principal = self.principal
        is_admin = bool(getattr(principal, "is_admin", False)) if principal is not None else False
        expected_tenant = (
            (getattr(principal, "tenant_id", None) or "default")
            if principal is not None
            else "default"
        )

        viewer = viewer_of(principal)

        if not is_admin and raw.get("tenant_id") != expected_tenant:
            raise PermissionError("Access denied: Tenant mismatch")

        # 票 63 半边 2：信函属主闸（单播信函仅属主或 admin 可签收；广播豁免）
        target = raw.get("receiver_agent_id")
        if (
            not is_admin
            and target != "*"
            and (raw.get("user_id") != viewer or target != receiver_agent_id)
        ):
            raise PermissionError(
                f"Agent '{receiver_agent_id}' is not the recipient of letter '{letter_id}'"
            )

        # 票 63 半边 3：状态机——终态不可翻转，同向幂等返回现态
        current = raw.get("status", "pending")
        new_status = "acknowledged" if action == "ack" else "rejected"
        if current == new_status:
            # 幂等重入：不假设首次签收的结果，只认状态（如实，不造假新时间戳）
            return {
                "letter_id": letter_id,
                "status": current,
                "receiver_agent_id": receiver_agent_id,
                "acknowledged_at": raw.get("acknowledged_at"),
                "already_" + new_status: True,
            }
        if current in ("acknowledged", "rejected"):
            raise ValueError(
                f"Letter '{letter_id}' is in terminal state '{current}'; "
                f"transition to '{new_status}' is not allowed"
            )

        raw["status"] = new_status
        raw["acknowledged_at"] = iso_utc(utcnow())

        return {
            "letter_id": letter_id,
            "status": new_status,
            "receiver_agent_id": receiver_agent_id,
            "acknowledged_at": raw["acknowledged_at"],
        }
