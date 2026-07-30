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
    embedding: list = Field(default_factory=list, sa_column=Column(JSON))
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
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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
