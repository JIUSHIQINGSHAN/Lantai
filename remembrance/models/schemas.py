from pydantic import BaseModel, Field, field_validator
from typing import Optional


class AddMemoryReq(BaseModel):
    source_type: str = "manual"
    title: str = Field(min_length=1, max_length=500)
    url: str = ""
    content: str = Field(min_length=10, max_length=50000)
    authors: list[str] = []
    tags: list[str] = []
    lane: str = Field(default="general")  # fact/rule/experience/preference/chat/general
    metadata: dict = {}  # 附加元数据，落 RawDocument.meta（如 source=pre_compress）

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, v):
        """有界校验：扁平、小键、值仅标量，防认证客户端耗尽 DB/磁盘。"""
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


class SearchReq(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(5, ge=1, le=100)
    memory_types: list[str] = []
    lanes: list[str] = []
    use_rerank: bool = True


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


class SourceReq(BaseModel):
    kind: str
    config: dict
    enabled: bool = True
