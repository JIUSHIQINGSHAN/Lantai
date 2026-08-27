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


def batch_reject(req: BatchRejectRequest, *, actor: str = "console") -> BatchActionResult:
    """跨领域批量拒绝：每项独立提交，诚实返回部分失败。"""
    reason = req.reason.strip()
    succeeded: list[dict] = []
    failed: list[dict] = []
    for ref in req.items:
        try:
            if ref.kind == "candidate":
                from lantai.services.candidate_service import review_candidate
                result = review_candidate(ref.source_id, approve=False, reason=reason)
            elif ref.kind == "proposal":
                from lantai.services.evolution_service import decide_proposal
                result = decide_proposal(
                    ref.source_id, ProposalDecisionReq(approve=False, reason=reason))
            elif ref.kind == "parameter":
                from lantai.parameters.service import decide_suggestion
                result = decide_suggestion(
                    ref.source_id,
                    DecisionRequest(decision="rejected", note=reason), actor).model_dump()
            else:
                from lantai.services.crystal_service import decide_crystal
                result = decide_crystal(ref.source_id, approve=False, reason=reason)
            succeeded.append({"kind": ref.kind, "source_id": ref.source_id,
                              "result": result})
        except Exception as exc:
            failed.append({"kind": ref.kind, "source_id": ref.source_id,
                           "error": _error_text(exc)})
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)


def batch_defer(req: BatchDeferRequest) -> BatchActionResult:
    """候选批量延期，状态变化的项目单独失败。"""
    from lantai.services.candidate_service import defer_candidate
    succeeded: list[dict] = []
    failed: list[dict] = []
    for ref in req.items:
        try:
            result = defer_candidate(
                ref.candidate_id, req.days, req.reason, ref.expected_review_due_at)
            succeeded.append({"kind": "candidate", "source_id": ref.candidate_id,
                              "result": result})
        except Exception as exc:
            failed.append({"kind": "candidate", "source_id": ref.candidate_id,
                           "error": _error_text(exc)})
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)


def batch_organize(req: BatchOrganizeRequest) -> BatchActionResult:
    """未分类记忆批量挂载到同一分类树节点。"""
    from lantai.services.tree_service import assign_memory_to_node
    succeeded: list[dict] = []
    failed: list[dict] = []
    for memory_id in req.memory_ids:
        try:
            result = assign_memory_to_node(memory_id, req.node_path)
            succeeded.append({"kind": "memory", "source_id": memory_id,
                              "result": result})
        except Exception as exc:
            failed.append({"kind": "memory", "source_id": memory_id,
                           "error": _error_text(exc)})
    return BatchActionResult(ok=not failed, succeeded=succeeded, failed=failed)

