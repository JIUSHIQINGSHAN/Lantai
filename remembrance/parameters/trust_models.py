"""
论文质量信号数据模型（方向一）——可信度体系 L0 层。

表结构沿项目风格：id 为 str + new_id()，JSON 用 Column(JSON)。
来源锁：signal_source 固定 "arxiv_atom"，service 写入时断言，其他写入路径不开放。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlmodel import SQLModel, Column, JSON, Field

from remembrance.core.time import utcnow


class PaperQualitySignal(SQLModel, table=True):
    """arXiv 结构化质量信号（唯一写入口：arxiv 适配器，signal_source 断言）。"""
    __tablename__ = "paper_quality_signal"

    id: str = Field(primary_key=True)
    raw_document_id: str = Field(unique=True, index=True,
                                 foreign_key="rawdocument.id")
    arxiv_id: str = Field(index=True)
    version: int = 1
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    comment_raw: Optional[str] = None
    journal_ref: Optional[str] = None
    doi: Optional[str] = None
    primary_category: str = ""
    categories: list = Field(default_factory=list, sa_column=Column(JSON))
    authors: list = Field(default_factory=list, sa_column=Column(JSON))
    author_count: int = 0
    venue_class: str = "unknown"          # journal|top_conf|other_peer|workshop|preprint|unknown
    tier: str = "D"                       # A|B|C|D（只降不升）
    tier_reason: list = Field(default_factory=list, sa_column=Column(JSON))
    staleness_level: str = "fresh"        # fresh|warn|blocked
    signal_source: str = "arxiv_atom"     # 来源锁
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- 视图模型（只读投影）

class QualitySignalView(BaseModel):
    """注入 prompt & 队列详情的只读信号视图。"""
    model_config = ConfigDict(extra="forbid")

    source_id: str
    arxiv_id: str
    venue_class: str
    evidence_tier: str
    published_at: Optional[datetime] = None
    version: int = 1
    age_days: int = 0
    staleness_level: str = "fresh"
    primary_evidence_eligible: bool = True
    tier_reason: list[str] = []


class GatingPolicy(BaseModel):
    """按 tier 解析出的门控策略（LLM 可见 tier 值，权重表永不外露）。"""
    model_config = ConfigDict(extra="forbid")

    tier: str
    tier_weight: float = 1.0
    quorum_required: int = 1
    delta_budget_factor: float = 1.0
    observation_days: int = 5


class QualitySignalRow(BaseModel):
    """signal_service 内部聚合（tier 与 staleness 已合并降级）。"""
    model_config = ConfigDict(extra="forbid")

    view: QualitySignalView
    gating: GatingPolicy


class ParamContradictionReport(SQLModel, table=True):
    """矛盾报告（方向四）：矛盾参数只能 acknowledge/close，接口层禁止 apply。"""
    __tablename__ = "param_contradiction_report"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="param_advice_run.id")
    param_key: str = Field(index=True)
    nature: str = "direction"
    side_a: dict = Field(default_factory=dict, sa_column=Column(JSON))
    side_b: dict = Field(default_factory=dict, sa_column=Column(JSON))
    scope_note: str = ""
    status: str = Field(default="open", index=True)  # open | acknowledged | closed
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
