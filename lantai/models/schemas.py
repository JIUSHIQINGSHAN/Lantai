from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_metadata_dict(v):
    """有界校验：扁平、小键、值仅标量，防认证客户端耗尽 DB/磁盘（Add/Raw 共用）。"""
    if not isinstance(v, dict):
        raise ValueError("metadata must be a dict")
    if len(v) > 10:
        raise ValueError("metadata too many keys (max 10)")
    for k, val in v.items():
        if not isinstance(k, str) or not k or len(k) > 64:
            raise ValueError("metadata key must be a short non-empty str")
        if isinstance(val, str):
            if len(val) > 500:
                raise ValueError("metadata string value too long (max 500)")
        elif val is not None and not isinstance(val, (int, float, bool)):
            raise ValueError("metadata value must be scalar (str/int/float/bool/None)")
    return v


class AddMemoryReq(BaseModel):
    source_type: str = "manual"
    title: str = Field(min_length=1, max_length=500)
    url: str = ""
    content: str = Field(default="", min_length=0, max_length=50000)
    media_url: str = Field(default="", max_length=15_000_000)  # 目识（vision）：图片地址/data URI（v0.10，v0.12 截屏放宽至 15MB 字符）
    authors: list[str] = []
    tags: list[str] = []
    lane: str = Field(default="general")  # fact/rule/experience/preference/chat/general
    domain: str | None = None          # 辨域（ADR-0034）：user/session/agent
    metadata: dict = {}  # 附加元数据，落 RawDocument.meta（如 source=pre_compress）

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, v):
        """有界校验：扁平、小键、值仅标量，防认证客户端耗尽 DB/磁盘。"""
        return _validate_metadata_dict(v)

    @model_validator(mode="after")
    def _check_content_or_media(self):
        """目识（vision）二选一：media_url 提供时 content 必须为空（图记忆
        由视觉描述自动生成）；两者皆空或同时提供都拒绝（宁 miss 不脏写，
        不静默丢弃 caption 或用户正文）。"""
        has_media = bool((self.media_url or "").strip())
        has_content = bool((self.content or "").strip())
        if not has_media and not has_content:
            raise ValueError("content required (or media_url for vision memory)")
        if has_media and has_content:
            raise ValueError("media_url 与 content 二选一：图记忆由视觉描述自动生成")
        if has_content and len(self.content.strip()) < 10:
            raise ValueError("content must be at least 10 characters")
        return self


class SearchReq(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(5, ge=1, le=100)
    memory_types: list[str] = []
    lanes: list[str] = []
    domain: str | None = None  # 辨域（ADR-0034）：user/session/agent/all
    use_rerank: bool = True
    force: bool = False  # 显式透传：为 True 时绕过相关性闸门直接检索（拾遗 ADR-0028）


class GateReq(BaseModel):
    candidate_id: str


class ProposalDecisionReq(BaseModel):
    approve: bool
    reason: str = ""


class FeedbackReq(BaseModel):
    memory_id: str
    query: str = ""
    helped: bool = False
    user_accepted: bool = False
    hallucination_risk: float = 0.0


class CandidateReviewReq(BaseModel):
    approve: bool
    reason: str = ""


class CandidateDeferReq(BaseModel):
    days: Literal[3, 7]
    reason: str = ""
    expected_review_due_at: datetime | None = None


class CandidateDeferUndoReq(BaseModel):
    expected_review_due_at: datetime | None = None


class ImportJsonlReq(BaseModel):
    """冷启动导入请求：JSONL 文本（每行一个 JSON 对象）。"""

    text: str = Field(min_length=1, max_length=50_000_000)


class SourceReq(BaseModel):
    kind: str
    config: dict
    enabled: bool = True



class RawMemoryReq(BaseModel):
    """原文直存请求（verbatim 记忆）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""

    title: str = ""
    content: str = Field(min_length=1, max_length=200_000)
    lane: str = Field(default="general")  # 默认取 settings.RAW_MEMORY_DEFAULT_LANE
    tags: list[str] = []
    metadata: dict = {}

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, v):
        return _validate_metadata_dict(v)

class ObsidianSyncReq(BaseModel):
    """Obsidian 笔记同步请求（Ticket 02）：原文直存 + [[双链]] 实体/边沉淀。"""

    title: str = ""
    content: str = Field(min_length=1, max_length=500_000)
    lane: str = Field(default="general")
    tags: list[str] = []
    metadata: dict = {}

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, v):
        return _validate_metadata_dict(v)


class TreeAddNodeReq(BaseModel):
    """新增分类树节点（v0.7）。"""
    name: str
    parent_path: str = "/"
    description: str = ""


class TreeAssignReq(BaseModel):
    """把记忆挂到分类树节点。"""
    memory_id: str
    node_path: str


class TreeUnassignReq(BaseModel):
    """解除记忆挂载。"""
    memory_id: str


class CrystalDecideReq(BaseModel):
    """技能结晶候选裁决：approve 必须带非空 steps（宁 miss 不脏写）。"""
    approve: bool
    steps: list[str] = []
    reason: str = ""


class SetPersonaReq(BaseModel):
    """设置/更新器识（Persona 人格基座，ADR-0029）。"""
    name: str = Field(min_length=1, max_length=100)
    linguistic_style: str = Field(default="", max_length=2000)
    guidelines: str = Field(default="", max_length=2000)
    epistemic_facts: str = Field(default="", max_length=2000)
    is_active: bool = True

