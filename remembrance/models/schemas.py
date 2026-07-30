from pydantic import BaseModel, Field
from typing import Optional


class AddMemoryReq(BaseModel):
    source_type: str = "manual"
    title: str = Field(min_length=1, max_length=500)
    url: str = ""
    content: str = Field(min_length=10, max_length=50000)
    authors: list[str] = []
    tags: list[str] = []
    lane: str = Field(default="general")  # fact/rule/experience/preference/chat/general


class SearchReq(BaseModel):
    query: str
    top_k: int = 5
    memory_types: list[str] = []
    lanes: list[str] = []


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
