"""案牍（WorkItem）控制台投影 DTO。"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


WorkItemKind = Literal[
    "candidate", "proposal", "conflict", "parameter", "crystal", "memory", "worker"
]
WorkItemSection = Literal[
    "immediate_action", "pending_decisions", "organization_needed", "runtime_status"
]
WorkItemPriority = Literal["critical", "high", "normal", "low"]
WorkItemRisk = Literal["critical", "high", "medium", "low"]


class WorkItem(BaseModel):
    id: str
    kind: WorkItemKind
    source_id: str
    title: str
    summary: str = ""
    section: WorkItemSection
    priority: WorkItemPriority
    reason: str
    risk: WorkItemRisk = "low"
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    due_at: datetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)
    related_refs: list[dict[str, str]] = Field(default_factory=list)
    group_id: str | None = None


class WorkItemListResponse(BaseModel):
    items: list[WorkItem]
    total: int
    counts: dict[str, int]
    limit: int
    offset: int


class WorkItemDetailResponse(BaseModel):
    item: WorkItem
    source: dict[str, Any]
    related: dict[str, Any] = Field(default_factory=dict)


class BatchItemRef(BaseModel):
    kind: Literal["candidate", "proposal", "parameter", "crystal"]
    source_id: str = Field(min_length=1, max_length=200)


class BatchRejectRequest(BaseModel):
    items: list[BatchItemRef] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class BatchCandidateRef(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    expected_review_due_at: datetime | None = None


class BatchDeferRequest(BaseModel):
    items: list[BatchCandidateRef] = Field(min_length=1, max_length=100)
    days: Literal[3, 7]
    reason: str = Field(default="", max_length=500)


class BatchOrganizeRequest(BaseModel):
    memory_ids: list[str] = Field(min_length=1, max_length=100)
    node_path: str = Field(min_length=1, max_length=500)


class BatchActionResult(BaseModel):
    ok: bool
    succeeded: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
