"""案牍批量命令；逐项调用领域 service，不建立万能 CRUD。"""

from fastapi import HTTPException

from lantai.models.schemas import ProposalDecisionReq
from lantai.models.work_items import (
    BatchActionResult,
    BatchDeferRequest,
    BatchOrganizeRequest,
    BatchRejectRequest,
)
from lantai.parameters.schemas import DecisionRequest


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc)


def batch_reject(
    req: BatchRejectRequest, *, actor: str = "console", principal=None
) -> BatchActionResult:
    """跨领域批量拒绝：每项独立提交，诚实返回部分失败。

    归属（票 .scratch/readside-gaps/02）：批量**不放宽**单条校验——他人的
    候选/提案照样进 `failed`、`ok=False`（同 `batch_organize` 口径）。
    parameter / crystal 两个分支无归属列，是系统级运维事实，不按人分。
    """
    reason = req.reason.strip()
    succeeded: list[dict] = []
    failed: list[dict] = []
    for ref in req.items:
        try:
            if ref.kind == "candidate":
                from lantai.services.candidate_service import review_candidate

                result = review_candidate(
                    ref.source_id, approve=False, reason=reason, principal=principal
                )
            elif ref.kind == "proposal":
                from lantai.services.evolution_service import decide_proposal

                result = decide_proposal(
                    ref.source_id,
                    ProposalDecisionReq(approve=False, reason=reason),
                    principal=principal,
                )
            elif ref.kind == "parameter":
                from lantai.parameters.service import decide_suggestion

                result = decide_suggestion(
                    ref.source_id, DecisionRequest(decision="rejected", note=reason), actor
                ).model_dump()
            else:
                from lantai.services.crystal_service import decide_crystal

                result = decide_crystal(ref.source_id, approve=False, reason=reason)
            succeeded.append({"kind": ref.kind, "source_id": ref.source_id, "result": result})
        except Exception as exc:
            failed.append({"kind": ref.kind, "source_id": ref.source_id, "error": _error_text(exc)})
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)


def batch_defer(req: BatchDeferRequest, *, principal=None) -> BatchActionResult:
    """候选批量延期，状态变化的项目单独失败。

    归属（票 .scratch/readside-gaps/02）：同 `batch_reject`——他人的候选
    进 `failed`，不被改到期日。
    """
    from lantai.services.candidate_service import defer_candidate

    succeeded: list[dict] = []
    failed: list[dict] = []
    for ref in req.items:
        try:
            result = defer_candidate(
                ref.candidate_id,
                req.days,
                req.reason,
                ref.expected_review_due_at,
                principal=principal,
            )
            succeeded.append({"kind": "candidate", "source_id": ref.candidate_id, "result": result})
        except Exception as exc:
            failed.append(
                {"kind": "candidate", "source_id": ref.candidate_id, "error": _error_text(exc)}
            )
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)


def batch_organize(req: BatchOrganizeRequest, *, principal=None) -> BatchActionResult:
    """未分类记忆批量挂载到同一分类树节点。

    principal 透传给 assign_memory_to_node（票 ownership-gaps/05）：批量
    不放宽单条的归属校验——他人记忆照样进 `failed`，`ok=False`。
    """
    from lantai.services.tree_service import assign_memory_to_node

    succeeded: list[dict] = []
    failed: list[dict] = []
    for memory_id in req.memory_ids:
        try:
            result = assign_memory_to_node(memory_id, req.node_path, principal=principal)
            succeeded.append({"kind": "memory", "source_id": memory_id, "result": result})
        except Exception as exc:
            failed.append({"kind": "memory", "source_id": memory_id, "error": _error_text(exc)})
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)
