from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Column, JSON

from remembrance.core.time import utcnow


class RawDocument(SQLModel, table=True):
    id: str = Field(primary_key=True)
    source_type: str
    source_id: str
    url: str
    title: str
    authors: list = Field(default_factory=list, sa_column=Column(JSON))
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=utcnow)
    lang: str = "en"
    content_hash: str = Field(index=True, unique=True)
    content: str = ""
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class DocumentChunk(SQLModel, table=True):
    id: str = Field(primary_key=True)
    document_id: str = Field(index=True)
    chunk_index: int
    text: str
    token_count: int = 0
    embedding: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class MemoryCandidate(SQLModel, table=True):
    id: str = Field(primary_key=True)
    document_id: str = Field(index=True)
    topic: list = Field(default_factory=list, sa_column=Column(JSON))
    summary: str = ""
    claims: list = Field(default_factory=list, sa_column=Column(JSON))
    methods: list = Field(default_factory=list, sa_column=Column(JSON))
    constraints: list = Field(default_factory=list, sa_column=Column(JSON))
    actions: list = Field(default_factory=list, sa_column=Column(JSON))
    contradictions: list = Field(default_factory=list, sa_column=Column(JSON))
    extractor_confidence: float = 0.0
    lane: str = Field(default="general")  # 分轨：从 AddMemoryReq 传入
    status: str = "new"
    created_at: datetime = Field(default_factory=utcnow)


class MemoryItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    memory_type: str = Field(index=True)
    namespace: str = Field(index=True, default="default")
    key: str = Field(index=True)
    content: str
    structure: dict = Field(default_factory=dict, sa_column=Column(JSON))
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    confidence: float = 0.5
    importance: float = 0.5
    tier: str = "working"
    lane: str = Field(default="general", index=True)         # 分轨：fact/rule/experience/preference/chat/general
    source_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    version: int = 1
    status: str = "active"
    last_used_at: Optional[datetime] = None
    use_count: int = 0
    helpful_count: int = 0
    decay_score: float = 1.0
    decay_class: str = "episodic"  # procedural(永不衰减)/semantic(慢)/episodic(快)；与 tier 正交
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MemoryEdge(SQLModel, table=True):
    id: str = Field(primary_key=True)
    source_memory_id: str = Field(index=True)
    target_memory_id: str = Field(index=True)
    relation: str  # supports / contradicts / refines / supersedes
    confidence: float = 0.5
    created_at: datetime = Field(default_factory=utcnow)


class CoreMemoryBlock(SQLModel, table=True):
    id: str = Field(primary_key=True)
    block: str = Field(index=True)
    namespace: str = Field(index=True, default="default")
    content: str = ""
    version: int = 1
    updated_at: datetime = Field(default_factory=utcnow)


class MemoryProposal(SQLModel, table=True):
    id: str = Field(primary_key=True)
    proposal_type: str
    target_memory_id: Optional[str] = None
    candidate_id: Optional[str] = None
    evidence_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    reason: str = ""
    proposed_patch: dict = Field(default_factory=dict, sa_column=Column(JSON))
    confidence: float = 0.0
    conflict_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "pending"
    decided_by: str = "auto"
    created_at: datetime = Field(default_factory=utcnow)
    applied_at: Optional[datetime] = None


class MemoryCheckpoint(SQLModel, table=True):
    id: str = Field(primary_key=True)
    memory_id: str = Field(index=True)
    version: int
    before: dict = Field(default_factory=dict, sa_column=Column(JSON))
    after: dict = Field(default_factory=dict, sa_column=Column(JSON))
    proposal_id: Optional[str] = None
    trigger: str = "manual"
    created_at: datetime = Field(default_factory=utcnow)


class MemoryUsageFeedback(SQLModel, table=True):
    id: str = Field(primary_key=True)
    memory_id: str = Field(index=True)
    query: str = ""
    session_id: str = ""
    used: bool = True
    helped: bool = False
    user_accepted: bool = False
    hallucination_risk: float = 0.0
    score_delta: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class Source(SQLModel, table=True):
    id: str = Field(primary_key=True)
    kind: str
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = True
    trust_score: float = 0.7
    last_fetched_at: Optional[datetime] = None


class IngestJob(SQLModel, table=True):
    id: str = Field(primary_key=True)
    source_id: str
    status: str = "pending"
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: str = ""


# ---------------------------------------------------------------- 参数建议（论文驱动优化）

class ParamAdviceRun(SQLModel, table=True):
    """一次 LLM 建议生成运行。"""
    __tablename__ = "param_advice_run"

    id: str = Field(primary_key=True)
    status: str = "processing"  # processing|suggested|abstained|failed
    source_document_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    base_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    base_snapshot_hash: str = ""
    registry_version: str = ""
    llm_output: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error_code: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class ParamAdvicePaper(SQLModel, table=True):
    """论文入队状态机：new|processing|retry|consumed|dead。"""
    __tablename__ = "param_advice_paper"

    id: str = Field(primary_key=True)
    raw_document_id: str = Field(
        index=True, unique=True, foreign_key="rawdocument.id")
    state: str = Field(default="new", index=True)
    attempt_count: int = 0
    run_id: Optional[str] = Field(default=None, index=True)
    available_at: datetime = Field(default_factory=utcnow)
    claimed_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ParamSuggestion(SQLModel, table=True):
    """参数调整建议（pending → accepted/rejected，禁止反向）。"""
    __tablename__ = "param_suggestion"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="param_advice_run.id")
    status: str = Field(default="pending", index=True)
    confidence: float = 0.0
    title: str = ""
    summary: str = ""
    rationale: str = ""
    expected_benefit: str = ""
    risk_notes: str = ""
    validation_plan: str = ""
    source_document_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list = Field(default_factory=list, sa_column=Column(JSON))
    changes: list = Field(default_factory=list, sa_column=Column(JSON))
    before_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    after_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    base_snapshot_hash: str = Field(index=True)
    registry_version: str = ""
    fingerprint: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None
    override_id: Optional[str] = Field(
        default=None, foreign_key="param_override.id")


class ParamOverride(SQLModel, table=True):
    """不可变追加式变更事件：apply / rollback。当前有效配置 = max(revision).after_snapshot。"""
    __tablename__ = "param_override"

    id: str = Field(primary_key=True)
    revision: int = Field(unique=True, index=True)
    operation: str = Field(index=True)  # apply | rollback
    suggestion_id: Optional[str] = Field(
        default=None, foreign_key="param_suggestion.id")
    rollback_of_override_id: Optional[str] = Field(
        default=None, foreign_key="param_override.id")
    before_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    after_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    before_snapshot_hash: str = ""
    after_snapshot_hash: str = Field(index=True)
    changes: list = Field(default_factory=list, sa_column=Column(JSON))
    registry_version: str = ""
    actor: str = ""
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class RetrievalEvent(SQLModel, table=True):
    """检索事件日志（方向二弱标注源）：哪条记忆被召回、当时生效参数、延迟。"""
    __tablename__ = "retrieval_event"

    id: str = Field(primary_key=True)
    trace_id: str = Field(index=True)
    query_text: str = ""
    query_norm_hash: str = Field(index=True)
    lane: str = ""
    intent_bucket: Optional[str] = Field(default=None, index=True)
    param_snapshot_hash: str = Field(index=True)  # 当时生效参数快照 hash
    result_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    result_scores: list = Field(default_factory=list, sa_column=Column(JSON))
    used_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    latency_ms: int = 0
    zero_result: bool = Field(default=False, index=True)
    is_system_noise: bool = Field(default=False, index=True)  # 系统注入噪音（技能库维护等），评估统计时排除
    created_at: datetime = Field(default_factory=utcnow, index=True)
