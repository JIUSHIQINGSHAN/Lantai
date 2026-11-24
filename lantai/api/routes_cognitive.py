"""
lantai/api/routes_cognitive.py
认知引擎 API 端点

POST /cognitive/observe       - 写入一条带证据的 Observation
POST /cognitive/reflect       - 触发一次 Reflection，返回 ReflectionReport
GET  /cognitive/context       - 返回结构化认知上下文（JSON 格式）
GET  /cognitive/context/prompt - 返回供 Agent 使用的 Markdown 认知上下文
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from lantai.cognition.context import CognitiveContextBuilder
from lantai.cognition.reflection import ReflectionEngine
from lantai.core.auth import get_current_user
from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.time import utcnow
from lantai.models.tables import CognitiveRole, Evidence, MemoryItem
from lantai.security.injection_guard import assert_no_prompt_injection
from lantai.storage.db import get_session_dep

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
    db: Session = Depends(get_session_dep),
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

    獬豸（v0.68 票 14）：`content` 自由文本直写 MemoryItem + Evidence 双落，
    且观测行是反思 LLM 的直接输入（reflection 扫描归纳 pattern 再进提案）——
    用户直写面走断言式（对齐 dialogue/verbatim 既有形态），ValueError → 422
    （routes_memory.py 既有范式；不补会把拒绝变成 500）。全仓复核无内部自动
    调用方（hermes-plugin/宿主脚本/workers 零命中），422 不会打断自动链路。
    """
    try:
        assert_no_prompt_injection(req.content)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
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
        valid_from=utcnow(),  # 主张自落库起生效（票 08：模型层默认 None，写入点显式声明）
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
    # 双通道索引（2026-11-24 修复）：此前只 commit 不建索引——`/cognitive/observe`
    # 写进来的记忆对 FTS 与向量两条召回通道**永久不可见**，只有向量失败时的
    # LIKE 兜底偶尔捞到。对照 `memory_service` 的正常写入路径（`sync_fts` +
    # `index_memory_item` 是同一套必备动作）。
    # FTS 同事务同步（强一致，ADR-0008 口径），向量在 commit 之后补：
    # 向量库是外部存储，写失败不该回滚已落库的记忆，只如实上报。
    from lantai.storage import db as db_module
    from lantai.storage.fts import sync_fts

    fts_synced = False
    if db_module.FTS_OK is not False:
        # FTS_OK 为 False = 初始化时已判定不可用（db.init_db 的降级分支），
        # 此时强行走 sync_fts 会把「降级但可用」变成 500。None 表示本进程没
        # 走过 init_db（测试/脚本直载），按可用尝试——真缺表时下面兜住。
        try:
            sync_fts(db, mem_id, mem.content)
            fts_synced = True
        except Exception as exc:
            logger.warning("cognitive observe FTS 同步失败（记忆仍落库，检索降级）: %s", exc)
    db.commit()

    index_warning = None
    try:
        from lantai.llm.client import embed
        from lantai.retrieval.hybrid import index_memory_item

        emb = embed([mem.content])[0]
        index_memory_item(
            mem_id,
            emb,
            {
                "key": mem.key,
                "memory_type": mem.memory_type,
                "lane": getattr(mem, "lane", "general") or "general",
                "domain": getattr(mem, "domain", "user") or "user",
                "shared_scope": getattr(mem, "shared_scope", "private") or "private",
                "tenant_id": getattr(mem, "tenant_id", "") or "",
                "user_id": getattr(mem, "user_id", "") or "",
                "session_id": getattr(mem, "session_id", "") or "",
                "agent_id": getattr(mem, "agent_id", "") or "",
            },
        )
    except Exception as exc:
        # 不静默：向量索引失败时该条只能靠 FTS/LIKE 召回，运维要看得见。
        logger.warning("cognitive observe 向量索引失败（FTS 已同步=%s）: %s", fts_synced, exc)
        index_warning = "vector_index_failed"

    payload = {"ok": True, "memory_id": mem_id, "evidence_id": ev_id}
    if index_warning:
        payload["warning"] = index_warning
    return payload


@router.post("/reflect")
def cognitive_reflect(
    db: Session = Depends(get_session_dep),
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
    票 61：`FailureRecord` 已有归属列（迁移 v43），失败蒸馏来源同样按
    viewer 收窄；`failures_scoped` 如实反映——具名用户 True，
    内部调用/worker/admin（全量口径）False。
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
    # 票 42：1..100——对齐 SearchReq.top_k 既有上界；0 会让 per-role 检索空转
    top_k: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_session_dep),
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
    # 票 42：1..100——对齐 SearchReq.top_k 既有上界
    top_k: int = Query(12, ge=1, le=100),
    db: Session = Depends(get_session_dep),
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
    # 票 42：max_* 0 合法（engine 有 >0 短路），只封大数与 char_budget 面积
    max_rules: int = Query(2, ge=0, le=50),
    max_failures: int = Query(1, ge=0, le=50),
    max_probes: int = Query(1, ge=0, le=50),
    char_budget: int = Query(350, ge=100, le=100000),
    db: Session = Depends(get_session_dep),
    ctx=Depends(get_current_user),
) -> dict:
    """
    自适应认知摘要（润物 RunwuEngine · v0.39）：
    返回给定 task 最相关的规则、失败教训与主动求证探针。

    比完整 /context 更轻量紧凑，供 MCP 自动注入和快速 Agent 查询使用。
    """
    from lantai.runtime.runwu import RunwuEngine

    is_admin = bool(getattr(ctx, "is_admin", False)) if ctx else False
    viewer = getattr(ctx, "user_id", None) if (ctx and not is_admin) else None
    tenant = getattr(ctx, "tenant_id", None) if (ctx and not is_admin) else None

    engine = RunwuEngine(db=db, default_char_budget=char_budget)
    res = engine.extract_summary(
        task=task,
        user_id=viewer,
        tenant_id=tenant,
        max_rules=max_rules,
        max_failures=max_failures,
        max_probes=max_probes,
        char_budget=char_budget,
    )

    # 结构化投影（保持向后兼容，同时扩展 probes 与紧凑 summary 文本）
    rules_out = [{"id": "", "content": r, "confidence": 0.85} for r in res.rules]
    failures_out = [{"id": "", "lesson": f, "severity": 0.7} for f in res.failures]
    probes_out = [{"question": p} for p in res.probes]

    return {
        "task": task,
        "rules": rules_out,
        "failures": failures_out,
        "probes": probes_out,
        "summary": res.formatted_text,
        "char_count": res.char_count,
    }
