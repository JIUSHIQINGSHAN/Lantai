"""
参数建议 Schema——LLM 输出模型与 API DTO。

全部 extra="forbid"：LLM 或请求体多出任何字段一律拒绝，防幻觉注入。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from remembrance.models.tables import (
    ParamOverride,
    ParamSuggestion,
)


# ---------------------------------------------------------------- LLM 输出

class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: str
    quote: str          # 必须是对应 RawDocument 内容的真实子串（归一化后）
    finding: str
    applicability: str


class ParamChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    before: float
    after: float
    reason: str


class SuggestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["suggest"] = "suggest"
    confidence: float = Field(ge=0.0, le=1.0)
    title: str
    summary: str
    rationale: str
    expected_benefit: str
    risk_notes: str
    validation_plan: str
    evidence: list[EvidenceItem] = Field(min_length=1)
    changes: list[ParamChange] = Field(min_length=1)


class AbstainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["abstain"] = "abstain"
    reason: str


ParamAdviceResult = Annotated[
    SuggestPayload | AbstainPayload,
    Field(discriminator="decision"),
]


# ---------------------------------------------------------------- API DTO

class SuggestionListItem(BaseModel):
    id: str
    status: str
    title: str
    confidence: float
    changes: list[ParamChange]
    base_snapshot_hash: str
    created_at: datetime


class SuggestionListResponse(BaseModel):
    items: list[SuggestionListItem]
    total: int
    limit: int
    offset: int


class SuggestionDetailResponse(BaseModel):
    id: str
    status: str
    confidence: float
    title: str
    summary: str
    rationale: str
    expected_benefit: str
    risk_notes: str
    validation_plan: str
    evidence: list[EvidenceItem]
    changes: list[ParamChange]
    before_snapshot: dict
    after_snapshot: dict
    base_snapshot_hash: str
    registry_version: str
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "rejected"]
    expected_base_snapshot_hash: str | None = None
    expected_revision: int | None = None
    note: str | None = None


class OverrideInfo(BaseModel):
    id: str
    revision: int
    operation: str


class DecisionResponse(BaseModel):
    suggestion_id: str
    status: str
    override: OverrideInfo | None = None
    current_process_applied: bool = False
    other_processes_max_delay_seconds: float = 0.0


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = None
    note: str | None = None


class RollbackResponse(BaseModel):
    rolled_back_override_id: str
    rollback_override: OverrideInfo
    effective_snapshot: dict


class OverrideListItem(BaseModel):
    id: str
    revision: int
    operation: str
    suggestion_id: str | None
    rollback_of_override_id: str | None
    actor: str
    note: str | None
    created_at: datetime


class OverrideListResponse(BaseModel):
    items: list[OverrideListItem]
    total: int
    limit: int
    offset: int


class RuntimeParamsResponse(BaseModel):
    snapshot: dict
    revision: int
    snapshot_hash: str
    registry_version: str


# ---------------------------------------------------------------- 表 -> DTO 转换

def suggestion_to_detail(s: ParamSuggestion) -> SuggestionDetailResponse:
    return SuggestionDetailResponse(
        id=s.id, status=s.status, confidence=s.confidence,
        title=s.title, summary=s.summary, rationale=s.rationale,
        expected_benefit=s.expected_benefit, risk_notes=s.risk_notes,
        validation_plan=s.validation_plan,
        evidence=[EvidenceItem(**e) for e in s.evidence],
        changes=[ParamChange(**c) for c in s.changes],
        before_snapshot=s.before_snapshot, after_snapshot=s.after_snapshot,
        base_snapshot_hash=s.base_snapshot_hash,
        registry_version=s.registry_version,
        created_at=s.created_at, decided_at=s.decided_at,
        decided_by=s.decided_by, decision_note=s.decision_note,
    )


def suggestion_to_list_item(s: ParamSuggestion) -> SuggestionListItem:
    return SuggestionListItem(
        id=s.id, status=s.status, title=s.title, confidence=s.confidence,
        changes=[ParamChange(**c) for c in s.changes],
        base_snapshot_hash=s.base_snapshot_hash, created_at=s.created_at,
    )


def override_to_list_item(o: ParamOverride) -> OverrideListItem:
    return OverrideListItem(
        id=o.id, revision=o.revision, operation=o.operation,
        suggestion_id=o.suggestion_id,
        rollback_of_override_id=o.rollback_of_override_id,
        actor=o.actor, note=o.note, created_at=o.created_at,
    )
