"""
lantai/api/routes_cognitive.py
认知引擎 API 端点

POST /cognitive/observe       - 写入一条带证据的 Observation
POST /cognitive/reflect       - 触发一次 Reflection，返回 ReflectionReport
GET  /cognitive/context       - 返回结构化认知上下文（JSON 格式）
GET  /cognitive/context/prompt - 返回供 Agent 使用的 Markdown 认知上下文
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from lantai.cognition.context import CognitiveContextBuilder
from lantai.cognition.reflection import ReflectionEngine
from lantai.core.auth import get_current_user
from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, Evidence, MemoryItem
from lantai.storage.db import get_session

router = APIRouter(prefix="/cognitive", tags=["cognitive"])


# ─────────────────────────────────────────────
# Request / Response Schemas
# ─────────────────────────────────────────────


class ObserveReq(BaseModel):
    content: str = Field(min_length=1, description="观测内容")
    evidence_type: str = Field(default="observation", description="证据类型")
    reliability: float = Field(default=0.7, ge=0.0, le=1.0, description="证据可靠性")
    independence: float = Field(default=1.0, ge=0.0, le=1.0, description="证据独立性（防复读）")
    provenance: dict = Field(default_factory=dict, description="来源溯源信息")


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────


@router.post("/observe")
def cognitive_observe(
    req: ObserveReq,
    db: Session = Depends(get_session),
    ctx=Depends(get_current_user),
) -> dict:
    """
    写入一条 Observation 记忆，同时创建对应的 Evidence 记录。
    Evidence 的 reliability × independence 复合权重防止 LLM 自我复读刷高置信度。

    归属（票 `.scratch/cognitive-write-gaps/01`）：此前**一个身份都不取**，
    落的两行 `MemoryItem` / `Evidence` 全是 `user_id=NULL`——而 NULL 在本仓
    的口径是"未记录归属的老行，人人可见"，于是 A 写的观测对所有用户可见。
    `content` 是自由文本，用户往里写什么完全不受控。

    四元组随 `ctx` 落列（`session_id` 不加：认知观测不是会话产物）。
    """
    mem_id = new_id("mem")
    ev_id = new_id("ev")

    mem = MemoryItem(
        id=mem_id,
        content=req.content,
        role=CognitiveRole.OBSERVATION,
        confidence=req.reliability * req.independence,  # 初始置信度 = 证据质量
        user_id=getattr(ctx, "user_id", None) or "default",
        tenant_id=getattr(ctx, "tenant_id", None),
        agent_id=getattr(ctx, "agent_id", None),
    )
    ev = Evidence(
        id=ev_id,
        evidence_type=req.evidence_type,
        source_memory_id=mem_id,
        content=req.content,
        reliability=req.reliability,
        independence=req.independence,
        provenance=req.provenance,
        created_at=utcnow(),
        user_id=getattr(ctx, "user_id", None) or "default",
        tenant_id=getattr(ctx, "tenant_id", None),
        agent_id=getattr(ctx, "agent_id", None),
    )

    db.add(mem)
    db.add(ev)
    db.commit()

    return {"ok": True, "memory_id": mem_id, "evidence_id": ev_id}


@router.post("/reflect")
def cognitive_reflect(
    db: Session = Depends(get_session),
    ctx=Depends(get_current_user),
) -> dict:
    """
    触发一次完整的 Reflection 循环：
    - 扫描重复 Observation → 归纳 Pattern 候选
    - 统计 FailureRecord
    - 检测置信度衰减的 Belief / Rule
    返回结构化的 ReflectionReport。

    归属（票 `.scratch/cognitive-write-gaps/01`）：`run_reflection` 的四个
    查询此前一个 scope 都没有——统计是全表数、A 与 B 的观测被**一起聚类**、
    晋升出的候选又是 ownerless 行。现在按 `ctx` 收窄（口径同票 04 的
    `_digest_scope`：`user_id == viewer OR IS NULL`，NULL 老行可见）。
    `FailureRecord` 无归属列，`failures` 计数无法收窄，报告里以
    `failures_scoped: false` 如实标注。
    """
    engine = ReflectionEngine(db, principal=ctx)
    report = engine.run_reflection()
    return {
        "new_patterns": report.new_patterns,
        "failure_patterns": report.failure_patterns,
        "belief_candidates": report.belief_candidates,
        "rule_candidates": report.rule_candidates,
        "rules_weakened": report.rules_weakened,
        "principles_under_review": report.principles_under_review,
        "contradictions": report.contradictions,
        "failures": report.failures,
        "failures_scoped": report.failures_scoped,
        "summary": report.summary,
    }


@router.get("/context")
def cognitive_context(
    task: str = "",
    top_k: int = 12,
    db: Session = Depends(get_session),
    ctx=Depends(get_current_user),
) -> dict:
    """
    返回结构化认知上下文（7 切面 JSON）：
    facts / experience / beliefs / rules / principles / failures / conflicts

    Agent 不再接收扁平的 Memory 列表，而是接收具有角色语义的分层上下文。
    """
    builder = CognitiveContextBuilder(db, principal=ctx)
    ctx = builder.build(task=task, top_k=top_k)
    return {
        "task": ctx.task,
        "facts": ctx.facts,
        "experience": ctx.experience,
        "beliefs": ctx.beliefs,
        "rules": ctx.rules,
        "principles": ctx.principles,
        "failures": ctx.failures,
        "conflicts": ctx.conflicts,
    }


@router.get("/context/prompt")
def cognitive_context_prompt(
    task: str = "",
    top_k: int = 12,
    db: Session = Depends(get_session),
    ctx=Depends(get_current_user),
) -> dict:
    """
    返回供 Agent system prompt 使用的 Markdown 格式认知上下文。
    """
    builder = CognitiveContextBuilder(db, principal=ctx)
    ctx = builder.build(task=task, top_k=top_k)
    return {"task": ctx.task, "prompt": ctx.to_prompt()}


@router.get("/summary")
def cognitive_summary(
    task: str = "",
    max_rules: int = 2,
    max_failures: int = 1,
    db: Session = Depends(get_session),
    ctx=Depends(get_current_user),
) -> dict:
    """
    轻量认知摘要（v0.4 Cognitive Middleware）：返回给定 task 最相关的 Rule 和 Failure。

    比完整 /context 更轻量，供 MCP 自动注入和快速 Agent 查询使用。
    响应格式：
    {
      "task": "...",
      "rules": [{"id": "...", "content": "...", "confidence": 0.85}],
      "failures": [{"id": "...", "lesson": "...", "severity": 0.7}],
      "summary": "相关规则: ... | 已知失败: ..."
    }
    """
    builder = CognitiveContextBuilder(db, principal=ctx)
    ctx = builder.build(task=task, top_k=max(max_rules, max_failures) + 2)

    rules_out = []
    for r in (ctx.rules or [])[:max_rules]:
        if isinstance(r, dict):
            rules_out.append(
                {
                    "id": r.get("id", ""),
                    "content": (r.get("content") or "")[:200],
                    "confidence": r.get("confidence", 0.5),
                }
            )
        else:
            rules_out.append(
                {
                    "id": getattr(r, "id", ""),
                    "content": (getattr(r, "content", "") or "")[:200],
                    "confidence": getattr(r, "confidence", 0.5),
                }
            )

    failures_out = []
    for f in (ctx.failures or [])[:max_failures]:
        if isinstance(f, dict):
            failures_out.append(
                {
                    "id": f.get("id", ""),
                    "lesson": f.get("content") or f.get("lesson") or "",
                    "severity": f.get("severity", 0.5),
                }
            )
        else:
            failures_out.append(
                {
                    "id": getattr(f, "id", ""),
                    "lesson": getattr(f, "lesson", None) or getattr(f, "content", ""),
                    "severity": getattr(f, "severity", 0.5),
                }
            )

    # 生成纯文本摘要
    parts = []
    if rules_out:
        parts.append("相关规则: " + "; ".join(r["content"][:80] for r in rules_out))
    if failures_out:
        lesson = failures_out[0].get("lesson", "")
        if lesson:
            parts.append(f"已知失败: {lesson[:80]}")
    summary_text = " | ".join(parts)

    return {
        "task": task,
        "rules": rules_out,
        "failures": failures_out,
        "summary": summary_text,
    }
