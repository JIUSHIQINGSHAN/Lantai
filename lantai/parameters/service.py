"""
参数建议 service 层——审阅/回滚的 CAS 事务（路由保持薄，业务在此）。

并发安全模型（SQLite 单写者 + 条件更新 + UNIQUE 兜底）：
- 批准：UPDATE ... WHERE status='pending' 的 rowcount==1 保证只成功一次；
        revision UNIQUE 兜底极端并发；同一事务插入 override + 更新 suggestion。
- 回滚：仅允许当前 head 且 head 为 apply；expected_revision 复核。
- 快照/基线过期：建议的 base_snapshot_hash 与当前 head 不符 → 409（由用户选择拒绝或保留）。
"""
from fastapi import HTTPException
from sqlalchemy import func, update
from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import (
    ParamOverride,
    ParamSuggestion,
)
from lantai.parameters.registry import default_snapshot, get_registry_version
from lantai.parameters.runtime import apply_snapshot_to_settings
from lantai.parameters.schemas import (
    DecisionRequest,
    DecisionResponse,
    OverrideInfo,
    OverrideListResponse,
    RollbackRequest,
    RollbackResponse,
    RuntimeParamsResponse,
    SuggestionDetailResponse,
    SuggestionListResponse,
    override_to_list_item,
    suggestion_to_detail,
    suggestion_to_list_item,
)
from lantai.parameters.validation import (
    ParamValidationError,
    snapshot_hash,
    validate_snapshot,
)
from lantai.storage import db


# ---------------------------------------------------------------- 查询

def list_suggestions(status: str | None = None, limit: int = 20,
                     offset: int = 0) -> SuggestionListResponse:
    with db.get_session() as s:
        query = select(ParamSuggestion)
        if status:
            query = query.where(ParamSuggestion.status == status)
        total = s.exec(
            select(func.count()).select_from(
                query.subquery())).one()
        items = s.exec(query.order_by(
            ParamSuggestion.created_at.desc()).limit(limit).offset(offset)).all()
        return SuggestionListResponse(
            items=[suggestion_to_list_item(i) for i in items],
            total=total, limit=limit, offset=offset)


def get_suggestion(suggestion_id: str) -> SuggestionDetailResponse:
    with db.get_session() as s:
        sug = s.get(ParamSuggestion, suggestion_id)
        if not sug:
            raise HTTPException(404, "suggestion_not_found")
        return suggestion_to_detail(sug)


def _head_override(session) -> ParamOverride | None:
    return session.exec(
        select(ParamOverride).order_by(ParamOverride.revision.desc())).first()


def _current_snapshot(session) -> dict:
    head = _head_override(session)
    return head.after_snapshot if head else default_snapshot()


def list_overrides(limit: int = 20, offset: int = 0) -> OverrideListResponse:
    with db.get_session() as s:
        total = s.exec(select(func.count()).select_from(ParamOverride)).one()
        items = s.exec(select(ParamOverride).order_by(
            ParamOverride.revision.desc()).limit(limit).offset(offset)).all()
        return OverrideListResponse(
            items=[override_to_list_item(i) for i in items],
            total=total, limit=limit, offset=offset)


def get_effective_params() -> RuntimeParamsResponse:
    with db.get_session() as s:
        head = _head_override(s)
        if head is None:
            snap = default_snapshot()
            return RuntimeParamsResponse(
                snapshot=snap, revision=0,
                snapshot_hash=snapshot_hash(snap),
                registry_version=get_registry_version())
        return RuntimeParamsResponse(
            snapshot=head.after_snapshot, revision=head.revision,
            snapshot_hash=head.after_snapshot_hash,
            registry_version=head.registry_version)


# ---------------------------------------------------------------- 审阅决策

def decide_suggestion(suggestion_id: str, req: DecisionRequest,
                      actor: str) -> DecisionResponse:
    with db.get_session() as s:
        sug = s.get(ParamSuggestion, suggestion_id)
        if not sug:
            raise HTTPException(404, "suggestion_not_found")
        if sug.status != "pending":
            raise HTTPException(409, "suggestion_already_decided")

        if req.decision == "rejected":
            sug.status = "rejected"
            sug.decided_at = utcnow()
            sug.decided_by = actor
            sug.decision_note = req.note
            s.add(sug)
            s.commit()
            return DecisionResponse(suggestion_id=suggestion_id,
                                    status="rejected")

        # ---- accepted：CAS 校验 + 原子写入 ----
        # 1) 基线复验：建议基于的快照仍须是当前有效配置
        head = _head_override(s)
        cur_snapshot = head.after_snapshot if head else default_snapshot()
        cur_hash = snapshot_hash(cur_snapshot)
        if sug.base_snapshot_hash != cur_hash:
            raise HTTPException(
                409, "snapshot_conflict: 当前参数基线已变化，建议基于旧快照，请拒绝或重新审阅")
        # 2) revision 复验：请求方以为的 head revision 必须一致
        cur_revision = head.revision if head else 0
        if req.expected_revision is not None \
                and req.expected_revision != cur_revision:
            raise HTTPException(409, f"revision_conflict: expected revision {req.expected_revision}, actual {cur_revision}")
        # 3) 注册表复验（三次校验中的批准前一次）
        try:
            validate_snapshot(sug.after_snapshot)
        except ParamValidationError as e:
            raise HTTPException(422, f"registry_validation_failed: {e}")

        # 4) CAS：条件更新保证只有一个批准者成功
        result = s.exec(update(ParamSuggestion).where(
            ParamSuggestion.id == suggestion_id,
            ParamSuggestion.status == "pending").values(
            status="accepted", decided_at=utcnow(),
            decided_by=actor, decision_note=req.note))
        if result.rowcount != 1:
            raise HTTPException(409, "suggestion_already_decided")

        # 5) 追加 apply 事件（revision UNIQUE 兜底极端并发）
        next_revision = cur_revision + 1
        before_hash = sug.base_snapshot_hash
        after_snapshot = dict(sug.after_snapshot)
        after_hash = snapshot_hash(after_snapshot)
        override = ParamOverride(
            id=new_id("pov"),
            revision=next_revision,
            operation="apply",
            suggestion_id=suggestion_id,
            before_snapshot=sug.before_snapshot,
            after_snapshot=after_snapshot,
            before_snapshot_hash=before_hash,
            after_snapshot_hash=after_hash,
            changes=sug.changes,
            registry_version=sug.registry_version,
            actor=actor,
            note=req.note,
        )
        s.add(override)
        s.flush()
        override_id = override.id
        sug.override_id = override_id
        s.add(sug)
        s.commit()

    # 事务提交后刷新本进程（其他进程由 5s 轮询收敛）
    applied = apply_snapshot_to_settings(after_snapshot)
    logger.info("param suggestion accepted: %s rev=%d (local applied=%s)",
                suggestion_id, next_revision, applied)
    return DecisionResponse(
        suggestion_id=suggestion_id, status="accepted",
        override=OverrideInfo(id=override_id, revision=next_revision,
                              operation="apply"),
        current_process_applied=applied,
        other_processes_max_delay_seconds=settings.PARAM_OVERRIDE_REFRESH_SECONDS)


# ---------------------------------------------------------------- 回滚

def rollback_override(override_id: str, req: RollbackRequest,
                      actor: str) -> RollbackResponse:
    with db.get_session() as s:
        head = _head_override(s)
        if head is None or head.id != override_id:
            raise HTTPException(409, "rollback_conflict: 目标不是当前 head override")
        if head.operation != "apply":
            raise HTTPException(409, "rollback_conflict: head 已是 rollback，不可再回滚旧 apply")
        if req.expected_revision is not None \
                and req.expected_revision != head.revision:
            raise HTTPException(409, f"rollback_conflict: expected revision {req.expected_revision}, actual {head.revision}")

        rollback = ParamOverride(
            id=new_id("pov"),
            revision=head.revision + 1,
            operation="rollback",
            rollback_of_override_id=head.id,
            before_snapshot=head.after_snapshot,
            after_snapshot=head.before_snapshot,
            before_snapshot_hash=head.after_snapshot_hash,
            after_snapshot_hash=head.before_snapshot_hash,
            changes=[],
            registry_version=head.registry_version,
            actor=actor,
            note=req.note,
        )
        s.add(rollback)
        s.commit()
        rollback_id = rollback.id
        rollback_revision = rollback.revision
        effective_snapshot = dict(rollback.after_snapshot)

    applied = apply_snapshot_to_settings(effective_snapshot)
    logger.info("param override rolled back: %s -> rev=%d (local applied=%s)",
                override_id, rollback_revision, applied)
    return RollbackResponse(
        rolled_back_override_id=override_id,
        rollback_override=OverrideInfo(id=rollback_id,
                                       revision=rollback_revision,
                                       operation="rollback"),
        effective_snapshot=effective_snapshot)
