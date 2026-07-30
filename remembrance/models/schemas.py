from pydantic import BaseModel
from typing import Optional


class AddMemoryReq(BaseModel):
    source_type: str = "manual"
    title: str
    url: str = ""
    content: str
    authors: list[str] = []
    tags: list[str] = []


class SearchReq(BaseModel):
    query: str
    top_k: int = 5
    memory_types: list[str] = []


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
