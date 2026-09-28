from sqlmodel import select

from lantai.core import scheduler as scheduler_mod
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.evolution.promoter import apply_proposal
from lantai.evolution.proposer import propose_from_candidate
from lantai.gate.decision import decide
from lantai.models.enums import ProposalStatus
from lantai.models.tables import MemoryCandidate, MemoryProposal
from lantai.services.candidate_service import enqueue_rejected
from lantai.storage import db


def _proposal_scope(principal):
    """演化候选/提案集的归属条件（票 `.scratch/proposal-apply-gaps/issues/16-*.md`）。

    admin / `principal=None` → None（不过滤）；否则
    `user_id == viewer OR IS NULL`，口径同票 12 的 `_kaogong_scope`。

    **NULL 老行放行**：真实库 636/657 行 memoryitem 是 `user_id IS NULL`
    （迁移前/脚本直插），判不可见会让单人部署下的演化整体空转。NULL 是
    「未记录」不是「属于所有人」。

    `principal=None`（scheduler 定时任务 / worker）保持全表——定时任务是
    系统行为，收窄成空转会让晋升与遗忘整体停摆。这个口径是刻意的。
    """
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (MemoryProposal.user_id == viewer) | (MemoryProposal.user_id.is_(None))


def _candidate_scope(principal):
    """候选集的归属条件（同 `_proposal_scope`，作用于 MemoryCandidate）。"""
    if principal is None or bool(getattr(principal, "is_admin", False)):
        return None
    from lantai.services.work_item_service import _viewer_of

    viewer = _viewer_of(principal)
    return (MemoryCandidate.user_id == viewer) | (MemoryCandidate.user_id.is_(None))


def run_evolve_once(principal=None):
    with db.get_session() as s:
        q = select(MemoryCandidate).where(MemoryCandidate.status.in_(["new", "fastpath"]))
        scope = _candidate_scope(principal)
        if scope is not None:
            q = q.where(scope)
        cands = s.exec(q).all()

    for cand in cands:
        result = decide(cand.id, principal=principal)
        if result["decision"] == "reject":
            # 校验失败（如低置信度）不再静默丢弃：进待审队列交用户裁决（Ticket 02）
            enqueue_rejected(cand.id)
            continue

        # archive_conflict（硬矛盾）不再丢弃新信息：走提案路径，
        # 由 proposer 生成 deprecate/update 纠正现有记忆。
        # 落地实战教训：旧记忆是错误提取（1116GB），新信息正确（16GB）——
        # 系统必须能"以新纠旧"，而不是把正确的纠正当矛盾丢掉。
        prop = propose_from_candidate(cand.id, result)
        if prop is None:
            # 目标不可寻址（update/merge/deprecate 的 target_key 解析不到唯一
            # active 记忆）——proposer 已留痕丢弃，此处继续下一个候选即可。
            # 不降级为 add：那会新建平行记忆（新旧矛盾脏写）。
            continue

        # 自动应用规则：置信度足够高且无强冲突 → 自动 apply
        if prop.confidence >= 0.7 and not prop.conflict_ids:
            apply_proposal(prop.id)
        else:
            logger.info("proposal %s pending human review", prop.id)
    # scene 增量聚类（ADR-0012 后续项）：消化期自动聚合无归属 active 记忆，
    # 替代"只靠手动 /scenes/rebuild"；SCENE_LAYER_ENABLED 门控，异常不影响演化
    if settings.SCENE_LAYER_ENABLED:
        from lantai.services.scene_service import assign_unassigned

        assign_unassigned()
    scheduler_mod.record_run("evolve")


def run_pending_proposals(principal=None):
    """执行已批准提案。

    归属（票 `.scratch/proposal-apply-gaps/issues/16-*.md`）：此前一个归属
    过滤都不带，专捞 `status == APPROVED` 的全表提案。**定时任务会替 A 把
    提案 apply 到 B 的记忆上**——路由层修得再好，这条路照样跨用户 apply。
    故候选集按归属收窄（口径同 `_proposal_scope`）。
    """
    with db.get_session() as s:
        q = select(MemoryProposal).where(MemoryProposal.status == ProposalStatus.APPROVED)
        scope = _proposal_scope(principal)
        if scope is not None:
            q = q.where(scope)
        props = s.exec(q).all()
    for p in props:
        apply_proposal(p.id, principal=principal)
