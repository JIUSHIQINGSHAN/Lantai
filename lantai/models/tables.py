from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Column, JSON

from lantai.core.time import utcnow


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
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 提取来源（prompt/model/时间，借鉴腾讯 provenance）
    lane: str = Field(default="general")  # 分轨：从 AddMemoryReq 传入
    status: str = "new"
    review_due_at: Optional[datetime] = None  # 待审队列 TTL 截止（Ticket 02）
    deferred_at: Optional[datetime] = None
    previous_review_due_at: Optional[datetime] = None
    defer_count: int = 0
    defer_reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class MemoryItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    memory_type: str = Field(index=True)
    namespace: str = Field(index=True, default="default")
    key: str = Field(index=True)
    content: str
    structure: dict = Field(default_factory=dict, sa_column=Column(JSON))
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 提取来源（prompt/model/时间，可溯源）
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    scene_id: Optional[str] = Field(default=None, index=True)  # 场景聚合归属（ADR-0012）
    tree_path: Optional[str] = Field(default=None, index=True)  # 分类树挂载路径（v0.7，借鉴 TreeMemory）
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




class MemoryScene(SQLModel, table=True):
    """场景聚合（ADR-0012，借鉴 TencentDB Agent Memory L2 场景层）。

    一组相关记忆的导航实体：name/summary 供导航注入，heat = 成员 use_count 求和
    （零写放大，重建时重算），渐进式披露时先给导航、需要详情再下钻。
    """

    id: str = Field(primary_key=True)
    name: str = Field(index=True)
    summary: str = ""
    heat: int = 0
    member_count: int = 0
    centroid: list = Field(default_factory=list, sa_column=Column(JSON))  # 场景质心（增量聚类：rebuild/assign 落库）

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
class MemoryEdge(SQLModel, table=True):
    id: str = Field(primary_key=True)
    source_memory_id: str = Field(index=True)
    target_memory_id: str = Field(index=True)
    relation: str  # supports / contradicts / refines / supersedes
    confidence: float = 0.5
    created_at: datetime = Field(default_factory=utcnow)



class MemoryNode(SQLModel, table=True):
    """记忆分类树节点（v0.7，借鉴 aiduMEI TreeMemory 窄版）。

    显式父子层级 + node_path 唯一路径（/projects/release），depth 前缀查询；
    记忆经 memoryitem.tree_path 挂载（显式 assign，不靠名字匹配）。
    """

    id: str = Field(primary_key=True)
    parent_id: Optional[str] = Field(default=None, index=True)
    name: str = Field(index=True)
    node_path: str = Field(index=True, unique=True)
    depth: int = 0
    description: str = ""
    namespace: str = Field(index=True, default="default")
    created_at: datetime = Field(default_factory=utcnow)


class SkillCrystal(SQLModel, table=True):
    """技能结晶候选项（v0.7，借鉴 aiduMEI SkillCrystallizer 窄版）。

    Mímir 铁律：LLM 只能建议不能直接 commit——检测只产 candidate，人工审核
    （decide approve 必须带 steps）后才落成 Skill 资产；宁 miss 不脏写。
    """

    id: str = Field(primary_key=True)
    skill_name: str = Field(index=True, unique=True)
    trigger_rule: str = ""
    procedure: str = ""
    source_lanes: list = Field(default_factory=list, sa_column=Column(JSON))
    sample_keys: list = Field(default_factory=list, sa_column=Column(JSON))
    hit_count: int = 1
    candidate_count: int = 0
    status: str = "candidate"  # candidate | approved | archived
    decision_reason: str = ""
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
    decision_reason: str = ""  # 裁决原因（用户/自动拒绝理由，反馈回路与回填校准输入）
    provenance: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 继承候选提取来源（可溯源）
    created_at: datetime = Field(default_factory=utcnow)
    applied_at: Optional[datetime] = None



class ConflictEvent(SQLModel, table=True):
    """冲突消解确定性层账本（P0-2）：规则命中即落账，可溯源、可裁决。"""

    id: str = Field(primary_key=True)
    memory_id: str = Field(index=True)  # 被判定冲突的既有记忆
    incoming_ref: str = ""             # 触发源：候选 summary / 待写入文本摘要
    rule_name: str = ""
    kind: str = "mutex"                # mutex（互斥规则） / override（属性覆盖，预留）
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "open"               # open / resolved / dismissed
    resolved_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None
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
    scene_ids: list = Field(default_factory=list, sa_column=Column(JSON))  # 命中记忆所属场景（可观测性）
    estimated_tokens: int = 0  # 查询 + 注入结果 token 粗估（成本观测）
    is_system_noise: bool = Field(default=False, index=True)  # 系统注入噪音（技能库维护等），评估统计时排除
    created_at: datetime = Field(default_factory=utcnow, index=True)

class SchedulerRun(SQLModel, table=True):
    """worker 上次运行时间（观察期保底：/stats 持久化 + 每日任务启动补跑判定）。"""
    __tablename__ = "scheduler_run"

    name: str = Field(primary_key=True)
    last_run_utc: str = ""


class ReflectRun(SQLModel, table=True):
    """反思运行记录（观察期可审计）：每次运行的水位/跳过/产出/LLM 失败/异常。

    与 scheduler_run（时间戳）互补：scheduler_run 回答「跑没跑」，
    reflect_run 回答「跑得怎么样」（空闲/产出/失败，回填校准去噪输入）。
    """
    __tablename__ = "reflect_run"

    id: str = Field(primary_key=True)
    run_at: datetime = Field(index=True)
    source: str = "unknown"   # scheduled | manual | unknown（旧记录迁移后保守标 unknown）
    waterline: float = 0.0
    skipped: str = ""            # "" = 正常执行（含空产出）；"idle" = 空闲跳过
    curate_failed: bool = False  # curator LLM 调用失败（宁 miss 空降级，但不静默）
    rejecter_failed: int = 0     # rejecter LLM 调用失败次数（异常按不通过处理，留痕不静默）
    health_before: dict = Field(default_factory=dict, sa_column=Column(JSON))
    health_after: dict = Field(default_factory=dict, sa_column=Column(JSON))
    proposals_created: int = 0
    auto_applied: int = 0
    pending: int = 0
    discarded: int = 0
    error: str = ""              # 未捕获异常时记录（调度器重试前留痕）


class SessionCheckpoint(SQLModel, table=True):
    """底本（ADR-0021，session checkpoint）：五段会话快照。

    五段块（cp_active_intent 在做 / cp_next_action 下一步 / cp_current_work 工作区 /
    cp_key_decisions 决策 / cp_open_notes 待办）——上下文压缩时写入，下次会话启动时
    注入（inject_checkpoint_context）；陈旧（> CHECKPOINT_STALENESS_DAYS）注入自动标注。
    同一 session 重写即替换（upsert 语义）；保留最近 CHECKPOINT_MAX_SESSIONS 个会话。
    """
    __tablename__ = "session_checkpoint"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    block_key: str = ""
    content: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class PersonaProfile(SQLModel, table=True):
    """器识（ADR-0029，Persona 人格基座）：L/G/E 三层认知模型。

    L (linguistic_style): 言语风格（诗词点缀、沉稳典雅）
    G (guidelines): 行为准则（宁 miss 不脏写、核心函数不 mock）
    E (epistemic_facts): 认知底色与核心事实（华硕天选三硬件环境、大哥为尊）
    """
    __tablename__ = "persona_profile"

    id: str = Field(primary_key=True)
    name: str = Field(index=True, unique=True)
    is_active: bool = Field(default=False, index=True)
    linguistic_style: str = ""
    guidelines: str = ""
    epistemic_facts: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
