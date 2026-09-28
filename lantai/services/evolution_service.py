"""演化与提案 service 层"""

from sqlmodel import select

from lantai.core.time import utcnow
from lantai.evolution.promoter import apply_proposal, rollback
from lantai.evolution.reflector import record_feedback
from lantai.models.enums import ProposalStatus
from lantai.models.schemas import FeedbackReq, ProposalDecisionReq
from lantai.models.tables import MemoryCheckpoint, MemoryItem, MemoryProposal
from lantai.storage import db
from lantai.workers.evolve_worker import run_evolve_once


def _proposal_viewer(principal) -> str:
    """读侧收敛到的 user_id（票 .scratch/readside-gaps/02）。

    口径同 `candidate_service._viewer_of`：None / 空 user_id → `"default"`。
    """
    return (
        (getattr(principal, "user_id", None) or "default") if principal is not None else "default"
    )


def _proposal_scope(principal):
    """归属过滤条件：非 admin 只见自己的提案；admin/system 全权（返回 None）。

    NULL 属主老行一律不可见（同 candidate_service 口径：真实库的提案行
    有属主，NULL 只可能是迁移前的历史行，放行就是把越权口子留着）。
    """
    if principal is not None and bool(getattr(principal, "is_admin", False)):
        return None
    return MemoryProposal.user_id == _proposal_viewer(principal)


def _ensure_can_decide(principal, proposal: MemoryProposal) -> None:
    """裁决提案的归属校验（票 .scratch/readside-gaps/02）。

    裁决 = 破坏性操作，复用 `acl.ensure_can_delete` 单一真源。此前 A 能
    对 B 的提案 decide——**approve 会直接 apply_proposal 写库**，比拒候选
    更重：A 能让 B 的待决内容凭空变成正式记忆。
    principal=None 仅限内部调用（CLI/worker/MCP），不校验。
    """
    if principal is None:
        return
    from lantai.core.acl import ensure_can_delete

    ensure_can_delete(
        principal,
        resource_user_id=proposal.user_id,
        resource_tenant_id=proposal.tenant_id,
    )


def list_proposals(status: str = "pending", limit: int = 50, *, principal=None) -> dict:
    """列出提案。

    归属收窄（票 .scratch/readside-gaps/02）：此前全表捞，`/proposals`
    把任何用户的待决提案（`reason` 常含记忆正文）吐给任何登录用户。
    """
    with db.get_session() as s:
        stmt = select(MemoryProposal).where(MemoryProposal.status == status)
        scope = _proposal_scope(principal)
        if scope is not None:
            stmt = stmt.where(scope)
        rows = s.exec(stmt.order_by(MemoryProposal.created_at.desc()).limit(limit)).all()
        return {"proposals": [r.model_dump(mode="json") for r in rows]}


def decide_proposal(proposal_id: str, req: ProposalDecisionReq, *, principal=None) -> dict:
    """批准或拒绝提案。

    归属校验（票 .scratch/readside-gaps/02）：approve 会 `apply_proposal`
    直接写库，A 能对 B 的提案点批准就等于能往库里塞内容。校验在 service
    层（worker/MCP 也走这条路）。
    """
    with db.get_session() as s:
        prop = s.get(MemoryProposal, proposal_id)
        if not prop:
            raise ValueError("proposal not found")
        if prop.status != ProposalStatus.PENDING:
            raise RuntimeError("proposal state changed; refresh and retry")
        if not req.approve and not (req.reason or "").strip():
            raise ValueError("reject reason is required")
        _ensure_can_decide(principal, prop)
        prop.decision_reason = req.reason or ""
        # decided_at（ADR-0053）：裁决即落时刻，不等 apply——approve 与 reject 同口径。
        # 巩固拒绝冷却期按此起算（ADR-0050 边界「冷却期起算点用 created_at 近似」的修法）。
        prop.decided_at = utcnow()
        if req.approve:
            prop.status = ProposalStatus.APPROVED
            prop.decided_by = "user"
            s.add(prop)
            s.commit()
            return apply_proposal(proposal_id)
        else:
            prop.status = ProposalStatus.REJECTED
            prop.decided_by = "user"
            s.add(prop)
            s.commit()
            return {"ok": True}


def do_rollback(memory_id: str, principal=None) -> dict:
    """回滚记忆。

    归属（票 .scratch/readside-gaps/13）：下传 principal 到 promoter.rollback，
    越权时该处返回 `{"ok": False, "reason": "forbidden: ..."}`。
    `principal=None`（worker/CLI/scheduler）保持全表。
    """
    return rollback(memory_id, principal=principal)


def record_feedback_entry(req: FeedbackReq, principal=None) -> dict:
    """记录反馈。

    归属（票 .scratch/readside-gaps/13）：同上，下传 principal。
    """
    return record_feedback(
        req.memory_id,
        req.query,
        req.helped,
        req.user_accepted,
        req.hallucination_risk,
        principal=principal,
    )


def run_evolve() -> dict:
    """运行演化 worker。"""
    run_evolve_once()
    return {"ok": True}


def list_checkpoints(memory_id: str, limit: int = 20, principal=None) -> dict:
    """列出指定记忆的检查点（回滚用的历史版本）。

    归属（票 `.scratch/readside-gaps/19`）：此前一个身份都不取，按 `memory_id`
    直查全表。而 `MemoryCheckpoint.before` / `.after` 是**完整行快照**
    （`content` / `title` / `structure` 全在里面），所以 A 能拿到 B 这条记忆的
    编年史——**比读当前版本更糟**：当前版本可能已改过，历史版本不会。
    且 checkpoint 是回滚的原料（票 13 已修 rollback 的归属，读侧这道口子一直开着）。

    **为什么 join `MemoryItem` 而不是「先取记忆行再过 `ensure_can_delete`」**
    （票面原优先级是反的，三条理由）：

    1. `consolidation_service.py:357` 故意写 `memory_id="cluster_consolidation"`
       这个**伪 id** 做沉潜留痕对账键（`:747` 直接查它），它**没有**
       `MemoryItem` 行。「先取记忆行」在它身上必然返回 None，此时 403（把
       「没有这条记忆」说成越权）、404（改变响应形状）、放行（泄漏）三个答案
       全是错的。**这是该方案的结构性缺陷，不是实现细节。**
    2. `ensure_can_delete` 的名字与语义都是破坏性操作（票 04 起），本票是
       **读**。读侧该用的判据是票 09 `_checkpoint_scope` 那种
       `user_id == viewer OR IS NULL`。
    3. join 天然处理两类无主行：孤儿 checkpoint（`delete_memory` 不级联删
       checkpoint，历史比行活得久）与伪 id——匹配不上就不返回。对非 admin
       这正是「宁 miss 不脏写」：没有属主可判，放行等于把已删记忆的编年史敞开。

    **不需要第二道行级校验**：票 17「边决定走哪条路、行决定露出什么」的两层
    结构在这里塌缩成一层——归属判定就在 SQL 的 join 条件里，读路径上**没有**
    `session.get(MemoryItem, …)` 这种按主键绕开 scope 的直读（票 14 踩过两次
    的盲区）。别照票 17 的样子再补一个 `_owns`，那是重复判据。

    NULL 口径同前 14 票读侧：`user_id == viewer OR user_id IS NULL`。
    单人部署下老记忆 `user_id` 为 NULL，判不可见会让历史整体消失。
    `principal=None`（worker/CLI/MCP）与 admin 一律不加 scope——**连 join
    都不写**，所以孤儿 checkpoint 与伪 id 对它们照见（沉潜对账与运维排查看
    的就是这两类）。

    **租户维度也收窄，但对 NULL 宽容**（`.scratch/readside-gaps/
    probe_tenant_semantics.py` 实证）：票 18 的跨租户用例证明「同 user_id、
    不同租户」是真形状（写侧 `ensure_can_delete` 挡它），读侧不挡就是同一道
    口子换个方向开。但不能写成严格 `tenant_id == viewer_tenant`——
    `auth.py:143` 的 `tenant_id` 是**客户端 header 自报**的，没有租户注册表、
    没有校验，严格相等会让同一个人**不带 header** 时连自己 `tenant_id` 为 NULL
    的老行都看不见（探针：`X-Tenant-Id=None` 时 m-1 凭空消失）。故取
    `ensure_can_delete` 的读侧同款判据：**双方都非空且不同才挡**，NULL 老行照旧可见。
    """
    with db.get_session() as s:
        q = select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == memory_id)
        if principal is not None and not bool(getattr(principal, "is_admin", False)):
            from lantai.services.work_item_service import _viewer_of

            viewer = _viewer_of(principal)
            cond = (MemoryItem.user_id == viewer) | (MemoryItem.user_id.is_(None))
            viewer_tenant = getattr(principal, "tenant_id", None)
            if viewer_tenant:
                # 双方都自报且不同才挡；NULL 老行照旧可见（见 docstring 末段）
                cond = cond & (
                    (MemoryItem.tenant_id == viewer_tenant) | (MemoryItem.tenant_id.is_(None))
                )
            q = q.join(MemoryItem, MemoryItem.id == MemoryCheckpoint.memory_id).where(cond)
        rows = s.exec(q.order_by(MemoryCheckpoint.version.desc()).limit(limit)).all()
        return {"checkpoints": [r.model_dump(mode="json") for r in rows]}
