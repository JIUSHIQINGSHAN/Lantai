"""Review ordinary MemoryItem candidates without treating them as intake candidates.

A review creates a new knowledge record and closes the source candidate. SQLite owns
status, FTS, checkpoints and the durable receipt. Vector writes are verified before
commit and compensated on failure; no automatic approval or batch policy lives here.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from lantai.core.acl import active_bindings, allowed_lanes, viewer_of
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import iso_utc, utcnow
from lantai.models.tables import Evidence, MemoryCheckpoint, MemoryEdge, MemoryItem
from lantai.security.injection_guard import check_prompt_injection
from lantai.storage import db
from lantai.storage.cas import cas_update
from lantai.storage.fts import fts_has_row, sync_fts
from lantai.storage.vector_store import get_vector_store

_REVIEW_KEY = "knowledge_candidate_review"


class KnowledgeCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review reason is required")
        return value.strip()

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, values: list[str]) -> list[str]:
        if any(not value or value != value.strip() or len(value) > 200 for value in values):
            raise ValueError("evidence ids must be nonempty identifiers")
        if len(set(values)) != len(values):
            raise ValueError("duplicate evidence ids")
        return sorted(values)


def knowledge_candidate_scope(principal) -> list:
    """Owner, tenant and lane scope shared by list, detail and review."""
    conditions = []
    if not bool(getattr(principal, "is_admin", False)):
        conditions.append(MemoryItem.user_id == viewer_of(principal))
        tenant = getattr(principal, "tenant_id", None)
        conditions.append(
            (MemoryItem.tenant_id == tenant) | MemoryItem.tenant_id.is_(None)
            if tenant
            else MemoryItem.tenant_id.is_(None)
        )
    lanes = getattr(principal, "allowed_lanes", None)
    agent = getattr(principal, "agent_id", None)
    if agent and active_bindings():
        lanes = allowed_lanes(agent) or []
    if lanes is not None:
        conditions.append(MemoryItem.lane.in_(lanes))
    return conditions


def _get_visible(s: Session, memory_id: str, principal) -> MemoryItem:
    row = s.exec(
        select(MemoryItem).where(MemoryItem.id == memory_id, *knowledge_candidate_scope(principal))
    ).first()
    if row is None:
        raise HTTPException(404, "knowledge candidate not found")
    return row


def _unit_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1


def _candidate_origin_sources(s: Session, items: list[MemoryItem]) -> set[str]:
    """批量判定「哪些来源是候选出身」（`is_candidate_origin` 的等价批量子集）。

    口径与 `record_ops_service.is_candidate_origin` 逐条一致：先看 provenance
    三类标记，未命中的再去 checkpoint 里找 `before/after.status == "candidate"`。
    差别只在**取数方式**：原实现每条来源发一次 checkpoint 查询（评审详情页
    N+1 的主项），这里一次 `IN` 拿全部命中 id，再按同一谓词判定。

    回归锁：`tests/test_opt_refactor_smoke.py` 只锁 MMR/预算/连接，批量这条路
    的正确性由 `tests/test_knowledge_candidate*.py` 的既有断言兜住。
    """
    if not items:
        return set()
    flagged: set[str] = set()
    need_checkpoint: list[str] = []
    for item in items:
        provenance = item.provenance or {}
        review = provenance.get("knowledge_candidate_review") or {}
        receipt = review.get("receipt") or {}
        if (
            item.status == "candidate"
            or "candidate_reconciliation" in provenance
            or (bool(review) and receipt.get("candidate_id", item.id) == item.id)
        ):
            flagged.add(item.id)
        else:
            need_checkpoint.append(item.id)
    if need_checkpoint:
        rows = s.exec(
            select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id.in_(need_checkpoint))
        ).all()
        for checkpoint in rows:
            if (checkpoint.before or {}).get("status") == "candidate" or (
                checkpoint.after or {}
            ).get("status") == "candidate":
                flagged.add(checkpoint.memory_id)
    return flagged


def _evidence_rows(s: Session, candidate: MemoryItem, principal) -> list[dict]:
    # An Evidence id in source_ids is supported as well as the usual MemoryItem
    # source ids. Only stored relationships qualify; callers cannot invent evidence.
    source_ids = {str(value) for value in candidate.source_ids or [] if value}
    if not source_ids:
        return []
    rows = s.exec(
        select(Evidence)
        .where((Evidence.source_memory_id.in_(source_ids)) | (Evidence.id.in_(source_ids)))
        .order_by(Evidence.id)
    ).all()

    # 批量取来源（原实现每条 evidence 一次 `select(MemoryItem)` —— 评审详情页
    # 的 N+1；scope 此前也在循环内每行重算，`knowledge_candidate_scope` 每次
    # 都会重建条件列表并复制 `AGENT_LANE_BINDINGS`）。判据完全不变：只换取数方式。
    needed_ids = {str(ev.source_memory_id) for ev in rows if ev.source_memory_id}
    scope = knowledge_candidate_scope(principal)
    sources_by_id: dict[str, MemoryItem] = {}
    if needed_ids:
        for item in s.exec(select(MemoryItem).where(MemoryItem.id.in_(needed_ids), *scope)).all():
            sources_by_id[item.id] = item
    candidate_origin = _candidate_origin_sources(s, list(sources_by_id.values()))

    result = []
    threshold = settings.GATE_MIN_EXTRACTOR_CONF
    for evidence in rows:
        source = sources_by_id.get(str(evidence.source_memory_id))
        if source is None or source.id == candidate.id:
            continue
        if not bool(getattr(principal, "is_admin", False)):
            if evidence.user_id not in (None, viewer_of(principal)):
                continue
            if evidence.tenant_id and evidence.tenant_id != getattr(principal, "tenant_id", None):
                continue
        reasons = []
        if evidence.status != "active":
            reasons.append("evidence is not active")
        # Consolidated / archived sources remain auditable. A withdrawn source or
        # another unreviewed candidate cannot bootstrap a claim into knowledge.
        if source.status not in ("active", "consolidated", "archived") or (
            source.id in candidate_origin
        ):
            reasons.append("source is not accepted evidence")
        if not evidence.content.strip():
            reasons.append("evidence content is empty")
        if not _unit_number(evidence.reliability) or evidence.reliability < threshold:
            reasons.append("evidence reliability below threshold")
        if not _unit_number(evidence.independence) or evidence.independence <= 0:
            reasons.append("evidence independence must be positive")
        hit, _ = check_prompt_injection(evidence.content)
        if settings.INJECTION_GUARD_ENABLED and hit:
            reasons.append("evidence rejected by injection guard")
        result.append(
            {
                **evidence.model_dump(mode="json"),
                "source_memory_status": source.status,
                "source_memory_version": source.version,
                "eligible": not reasons,
                "blocking_reasons": reasons,
            }
        )
    return result


def _quality(candidate: MemoryItem, evidence: list[dict], evidence_ids: list[str]) -> dict:
    reasons = []
    threshold = settings.GATE_MIN_EXTRACTOR_CONF
    min_sources = max(1, settings.PROMOTE_SEMANTIC_MIN_SOURCES)
    if not candidate.content.strip():
        reasons.append("candidate content is empty")
    if not _unit_number(candidate.confidence) or candidate.confidence < threshold:
        reasons.append("candidate confidence below threshold")
    if candidate.valid_to is not None:
        reasons.append("candidate has a closed validity interval")
    hit, _ = check_prompt_injection(candidate.content)
    if settings.INJECTION_GUARD_ENABLED and hit:
        reasons.append("candidate rejected by injection guard")
    by_id = {row["id"]: row for row in evidence}
    selected = [by_id[eid] for eid in evidence_ids if eid in by_id]
    if len(selected) != len(evidence_ids) or not evidence_ids:
        reasons.append("select stored evidence linked to this candidate")
    if any(not row["eligible"] for row in selected):
        reasons.append("selected evidence is not eligible")
    valid = [row for row in selected if row["eligible"]]
    independent_sources = len({row["source_memory_id"] for row in valid})
    quality = sum(row["reliability"] * row["independence"] for row in valid) / max(1, len(valid))
    if independent_sources < min_sources:
        reasons.append("insufficient independent evidence sources")
    if quality < threshold:
        reasons.append("evidence quality below threshold")
    return {
        "eligible": not reasons,
        "blocking_reasons": reasons,
        "confidence": candidate.confidence,
        "evidence_quality": round(quality, 6),
        "independent_sources": independent_sources,
        "min_confidence": threshold,
        "min_evidence_quality": threshold,
        "min_sources": min_sources,
        "evidence_ids": evidence_ids,
    }


def knowledge_candidate_detail(memory_id: str, *, principal=None) -> dict:
    """Read-only review material; exposes only evidence in the caller's scope."""
    with db.get_session() as s:
        candidate = _get_visible(s, memory_id, principal)
        if candidate.status != "candidate":
            raise HTTPException(404, "knowledge candidate is no longer pending")
        evidence = _evidence_rows(s, candidate, principal)
        recommended = [row["id"] for row in evidence if row["eligible"]]
        return {
            "source": candidate.model_dump(mode="json"),
            "evidence": evidence,
            "review": {
                **_quality(candidate, evidence, recommended),
                "expected_version": candidate.version,
                "recommended_evidence_ids": recommended,
            },
        }


def _replay(s: Session, checkpoint_id: str, fingerprint: str) -> dict | None:
    checkpoint = s.get(MemoryCheckpoint, checkpoint_id)
    if checkpoint is None:
        return None
    stored = (checkpoint.after.get("provenance") or {}).get(_REVIEW_KEY) or {}
    if stored.get("fingerprint") != fingerprint:
        raise HTTPException(409, "request_id was already used with a different review")
    return {**stored["receipt"], "idempotent": True}


def _snapshot(s: Session, candidate: MemoryItem, principal, req) -> dict:
    evidence = _evidence_rows(s, candidate, principal)
    quality = _quality(candidate, evidence, req.evidence_ids)
    if req.decision == "approve" and not quality["eligible"]:
        raise HTTPException(422, {"code": "quality_gate", **quality})
    return quality


def review_knowledge_candidate(
    memory_id: str,
    *,
    decision: str,
    reason: str,
    expected_version: int,
    request_id: str,
    evidence_ids: list[str] | None = None,
    principal=None,
) -> dict:
    """Authorized, CAS-protected review. A retry replays the durable receipt.

    401 missing identity; 404 invisible/missing; 409 stale/conflicting review;
    422 quality/input refusal; 503 storage/index failure with SQL rolled back.
    An uncertain commit is checked via its receipt before vector compensation.
    """
    if principal is None:
        raise HTTPException(401, "an explicit principal is required for candidate review")
    try:
        req = KnowledgeCandidateReviewRequest(
            decision=decision,
            reason=reason,
            expected_version=expected_version,
            request_id=request_id,
            evidence_ids=evidence_ids or [],
        )
    except ValidationError as exc:
        raise HTTPException(422, "invalid candidate review request") from exc
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                **req.model_dump(),
                "actor": viewer_of(principal),
                "tenant_id": getattr(principal, "tenant_id", None),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    checkpoint_id = "kcr_" + hashlib.sha256(f"{memory_id}\0{request_id}".encode()).hexdigest()

    with db.get_session() as s:
        candidate = _get_visible(s, memory_id, principal)
        replay = _replay(s, checkpoint_id, fingerprint)
        if replay:
            return replay
        if candidate.status != "candidate" or candidate.version != req.expected_version:
            raise HTTPException(409, "candidate status/version changed; refresh before reviewing")
        _snapshot(s, candidate, principal, req)
        preflight = candidate.model_dump(mode="json")
        content = candidate.content

    # Expensive external call precedes the write lock. The current record and all
    # evidence are revalidated after CAS has acquired the SQL writer transaction.
    embedding = None
    if req.decision == "approve":
        from lantai.llm.client import embed

        try:
            vectors = embed([content])
            embedding = vectors[0]
            if not embedding or any(not math.isfinite(float(v)) for v in embedding):
                raise ValueError("invalid embedding")
        except Exception as exc:
            raise HTTPException(503, {"code": "embedding_failed", "applied": False}) from exc

    memory_id_new = new_id("mem") if req.decision == "approve" else None
    vector_attempted = False
    store = None
    commit_attempted = False
    try:
        with db.get_session() as s:
            candidate = _get_visible(s, memory_id, principal)
            replay = _replay(s, checkpoint_id, fingerprint)
            if replay:
                return replay
            if not cas_update(
                s,
                MemoryItem,
                memory_id,
                {
                    "status": "candidate",
                    "version": req.expected_version,
                    "updated_at": candidate.updated_at,
                },
                {"version": req.expected_version + 1},
                *knowledge_candidate_scope(principal),
            ):
                s.rollback()
                replay = _replay(s, checkpoint_id, fingerprint)
                if replay:
                    return replay
                raise HTTPException(
                    409, "candidate status/version changed; refresh before reviewing"
                )
            s.refresh(candidate)
            # Version alone cannot detect legacy writers that do not bump it.
            locked = candidate.model_dump(mode="json")
            locked["version"] = req.expected_version
            if locked != preflight:
                raise HTTPException(
                    409, "candidate changed during review; refresh before reviewing"
                )
            quality = _snapshot(s, candidate, principal, req)
            now = utcnow()
            before = dict(preflight)
            candidate.updated_at = now
            candidate.status = "consolidated" if memory_id_new else "archived"
            candidate.lifecycle_status = "superseded" if memory_id_new else "retired"
            if memory_id_new:
                candidate.superseded_by = memory_id_new
                candidate.superseded_at = now
            else:
                candidate.retired_at = now
            receipt = {
                "ok": True,
                "decision": req.decision,
                "candidate_id": memory_id,
                "candidate_status": candidate.status,
                "version": candidate.version,
                "memory_id": memory_id_new,
                "request_id": req.request_id,
                "checkpoint_id": checkpoint_id,
                "idempotent": False,
                "quality": quality,
            }
            audit = {
                "request_id": req.request_id,
                "fingerprint": fingerprint,
                "decision": req.decision,
                "reason": req.reason,
                "actor": viewer_of(principal),
                "decided_at": iso_utc(now),
                "evidence_ids": req.evidence_ids,
                "receipt": receipt,
                "evidence_snapshot": [
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "source_memory_id",
                            "source_memory_status",
                            "source_memory_version",
                            "status",
                            "reliability",
                            "independence",
                        )
                    }
                    for row in _evidence_rows(s, candidate, principal)
                    if row["id"] in req.evidence_ids
                ],
            }
            candidate.provenance = {**(candidate.provenance or {}), _REVIEW_KEY: audit}
            s.add(candidate)
            sync_fts(s, candidate.id, None)
            if memory_id_new:
                knowledge = MemoryItem(
                    id=memory_id_new,
                    content=candidate.content,
                    memory_type="semantic",
                    role=candidate.role,
                    confidence=min(candidate.confidence, quality["evidence_quality"]),
                    reason=req.reason,
                    source_ids=list(dict.fromkeys([candidate.id] + candidate.source_ids)),
                    provenance={"source_candidate_id": candidate.id, _REVIEW_KEY: audit},
                    promotion_trace={**(candidate.promotion_trace or {}), "review": audit},
                    structure=candidate.structure,
                    tags=candidate.tags,
                    importance=candidate.importance,
                    namespace=candidate.namespace,
                    lane=candidate.lane,
                    domain=candidate.domain,
                    shared_scope=candidate.shared_scope,
                    tenant_id=candidate.tenant_id,
                    user_id=candidate.user_id,
                    agent_id=candidate.agent_id,
                    session_id=candidate.session_id,
                    status="active",
                    lifecycle_status="active",
                    decay_class="semantic",
                    tier="long_term",
                    valid_from=candidate.valid_from or now,
                    event_time=candidate.event_time,
                    event_time_precision=candidate.event_time_precision,
                    created_at=now,
                    updated_at=now,
                )
                s.add(knowledge)
                s.flush()
                sync_fts(s, knowledge.id, knowledge.content)
                if not fts_has_row(s, knowledge.id):
                    raise RuntimeError("FTS presence check failed")
                s.add(
                    MemoryCheckpoint(
                        id=new_id("ckpt"),
                        memory_id=knowledge.id,
                        version=1,
                        before={},
                        after=knowledge.model_dump(mode="json"),
                        trigger="knowledge_candidate_review",
                    )
                )
                s.add(
                    MemoryEdge(
                        id=new_id("edge"),
                        source_memory_id=candidate.id,
                        target_memory_id=knowledge.id,
                        relation="supports",
                        confidence=knowledge.confidence,
                        reason=req.reason,
                        user_id=knowledge.user_id,
                        tenant_id=knowledge.tenant_id,
                        agent_id=knowledge.agent_id,
                        session_id=knowledge.session_id,
                    )
                )
            s.add(
                MemoryCheckpoint(
                    id=checkpoint_id,
                    memory_id=candidate.id,
                    version=candidate.version,
                    before=before,
                    after=candidate.model_dump(mode="json"),
                    trigger="knowledge_candidate_review",
                )
            )
            s.flush()
            store = get_vector_store()
            # Candidates should never have a retrieval vector. Preserve any legacy
            # inconsistency on refusal rather than irreversibly deleting it before
            # a SQL commit which may still fail.
            if store.has(candidate.id):
                raise HTTPException(
                    409, "candidate already has a vector; reconcile its index before review"
                )
            if memory_id_new:
                vector_attempted = True
                store.add(
                    [knowledge.id],
                    [embedding],
                    [
                        {
                            "key": knowledge.key or "",
                            "memory_type": knowledge.memory_type,
                            "lane": knowledge.lane,
                            "domain": knowledge.domain,
                            "shared_scope": knowledge.shared_scope,
                            "user_id": knowledge.user_id or "",
                            "tenant_id": knowledge.tenant_id or "",
                            "agent_id": knowledge.agent_id or "",
                            "session_id": knowledge.session_id or "",
                        }
                    ],
                )
                if not store.has(knowledge.id):
                    raise RuntimeError("vector presence check failed")
            commit_attempted = True
            try:
                s.commit()
            except Exception:
                # SQLite deferred constraints can leave a failed COMMIT transaction
                # open on the pooled DBAPI connection. Session.close alone may return
                # that connection without rolling it back; a receipt read on it would
                # then see uncommitted data and report false success.
                s.rollback()
                raise
            return receipt
    except Exception as exc:
        if isinstance(exc, HTTPException) and not vector_attempted and not commit_attempted:
            raise
        # A commit can fail after the server accepted it. Consult the atomic receipt
        # before deleting a potentially committed knowledge vector.
        if commit_attempted:
            try:
                with db.get_session() as check:
                    replay = _replay(check, checkpoint_id, fingerprint)
                    if replay:
                        # A concurrent retry may have committed the same request
                        # after this attempt rolled back. It owns a different new
                        # memory id; remove only this attempt's provisional vector.
                        if vector_attempted and replay.get("memory_id") != memory_id_new:
                            try:
                                store.delete([memory_id_new])
                                if store.has(memory_id_new):
                                    raise RuntimeError("provisional vector cleanup failed")
                            except Exception as cleanup_exc:
                                raise HTTPException(
                                    503,
                                    {
                                        "code": "candidate_review_cleanup_failed",
                                        "applied": True,
                                        "request_id": request_id,
                                        "memory_id": replay.get("memory_id"),
                                        "orphan_vector_id": memory_id_new,
                                    },
                                ) from cleanup_exc
                        return replay
            except HTTPException as verify_exc:
                if verify_exc.status_code != 409:
                    raise
                # Conflicting payload may have won after this attempt rolled back.
                # Preserve that 409 only after compensating our provisional vector.
                exc = verify_exc
            except SQLAlchemyError as verify_exc:
                raise HTTPException(
                    503,
                    {
                        "code": "commit_outcome_unknown",
                        "applied": None,
                        "request_id": request_id,
                        "retry_same_request": True,
                    },
                ) from verify_exc
        cleanup = "not_needed"
        if vector_attempted and store is not None:
            try:
                store.delete([memory_id_new])
                cleanup = "removed" if not store.has(memory_id_new) else "failed"
            except Exception:
                cleanup = "failed"
        if isinstance(exc, HTTPException) and cleanup != "failed":
            raise exc
        raise HTTPException(
            503,
            {
                "code": "candidate_review_storage_failed",
                "applied": False,
                "request_id": request_id,
                "vector_cleanup": cleanup,
                "orphan_vector_id": memory_id_new if cleanup == "failed" else None,
            },
        ) from exc
