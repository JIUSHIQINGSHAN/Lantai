"""MCP Server——标准协议写操作（search/add/feedback）

与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）
标准 MCP JSON-RPC 2.0 协议
"""
import json
import os
import sys

# 使子进程无论 cwd 在哪都能 import lantai（Hermes 拉 MCP 时 cwd 不可控）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认用 GBK 解码 stdin/stdout；Hermes 按 UTF-8 写 JSON（含中文 query），
# 若按 GBK 读则中文全变乱码（如「你好」→「浣犲ソ」）→ 检索零命中、注入全失效。
# 必须在任何 stdin/stdout 读写前执行。Python 3.7+ reconfigure；旧版靠环境变量兜底。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from pydantic import ValidationError

from lantai.models.schemas import AddMemoryReq, SearchReq, FeedbackReq
from lantai.services.memory_service import add_memory
from lantai.services.evolution_service import record_feedback_entry
from lantai.retrieval.hybrid import hybrid_search
from lantai.gate.prefilter import relevance_check

PROTOCOL_VERSION = "2024-11-05"


def handle_search(params: dict) -> dict:
    query = params.get("query", "")
    if not isinstance(query, str) or not (1 <= len(query) <= 8000):
        raise ValueError("query must be a string of 1..8000 chars")
    top_k = params.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not (1 <= top_k <= 100):
        raise ValueError("top_k must be an int in [1, 100]")
    gate = relevance_check(query)
    if not gate["needs_memory"]:
        event_id = _try_log(query, [], 0, gate)
        return {"results": [], "gate": gate, "event_id": event_id}
    import time
    t0 = time.perf_counter()
    results = hybrid_search(query, top_k=top_k)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    event_id = _try_log(query, results, latency_ms, gate)
    # Ticket 04: 检索透明——命中来源说明（id + 摘要 + 分数）
    from lantai.retrieval.evidence import build_evidence
    return {"results": results, "gate": gate, "event_id": event_id,
            "evidence": build_evidence(results)}


def _try_log(query: str, results: list, latency_ms: int, gate: dict) -> str | None:
    """检索事件埋点（方向二）：失败零侵入。返回 event_id 供生成侧回填 used_ids。"""
    try:
        from lantai.observability.retrieval_log import log_retrieval
        return log_retrieval(query, results, latency_ms=latency_ms, gate=gate)
    except Exception:
        return None


def handle_backfill(params: dict) -> dict:
    """生成侧回填：Hermes 回答时实际用到的记忆 id 写回检索事件（弱标注）。"""
    event_id = params.get("event_id", "")
    used_ids = params.get("used_ids", [])
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if not isinstance(used_ids, list) or not all(isinstance(x, str) for x in used_ids):
        raise ValueError("used_ids must be a list of strings")
    from lantai.observability.retrieval_log import backfill_used_ids as _bf
    _bf(event_id, used_ids)
    return {"ok": True, "event_id": event_id, "used_count": len(used_ids)}


def handle_add(params: dict) -> dict:
    req = AddMemoryReq(
        title=params.get("title", ""),
        content=params.get("content", ""),
        lane=params.get("lane", "general"),
    )
    return add_memory(req)


def handle_add_dialogue(params: dict) -> dict:
    """对话写通道：对话文本 → 提取链（fastpath 直通 / 候选 / 闲聊入队）。"""
    text = params.get("text", "")
    user_id = params.get("user_id", "default")
    source = params.get("source", "dialogue")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    from lantai.ingestion.dialogue import ingest_dialogue
    return ingest_dialogue(text, user_id=user_id, source=source)


def handle_candidates_pending(params: dict) -> dict:
    """待审候选列表（Ticket 02）——被闸门拒绝的候选进此队列等人工裁决。"""
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from lantai.services.candidate_service import list_pending_candidates
    return list_pending_candidates(limit)


def handle_candidate_review(params: dict) -> dict:
    """审核候选：approve → 生成 pending 提案；reject → 归档。"""
    candidate_id = params.get("candidate_id", "")
    approve = params.get("approve", False)
    reason = params.get("reason", "")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be a non-empty string")
    if not isinstance(approve, bool):
        raise ValueError("approve must be a boolean")
    from lantai.services.candidate_service import review_candidate
    return review_candidate(candidate_id, approve=approve, reason=reason)


def handle_candidate_refine(params: dict) -> dict:
    """披沙：对单条候选记忆进行指代消解与提纯（ADR-0030）。"""
    candidate_id = params.get("candidate_id", "")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id must be a non-empty string")
    from lantai.services.refine_service import refine_candidate_record
    return refine_candidate_record(candidate_id.strip())


def handle_get_digest(params: dict) -> dict:
    """当日记忆盘点报告（Ticket 03）。"""
    from lantai.workers.digest_worker import load_today_digest
    return load_today_digest()


def handle_kaogong_eval(params: dict) -> dict:
    """考功：执行全库记忆价值演化考评周期（ADR-0031）。"""
    from lantai.services.kaogong_service import run_kaogong_cycle
    return run_kaogong_cycle()





def handle_feedback(params: dict) -> dict:
    req = FeedbackReq(
        memory_id=params.get("memory_id", ""),
        query=params.get("query", ""),
        helped=params.get("helped", False),
        user_accepted=params.get("user_accepted", False),
    )
    return record_feedback_entry(req)



def handle_raw_add(params: dict) -> dict:
    """原文直存（verbatim）：内容直入 FTS5+向量，零 LLM，不走提取/闸门/演化。"""
    from lantai.models.schemas import RawMemoryReq
    from lantai.services.memory_service import add_raw_memory
    content = params.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    req = RawMemoryReq(
        title=params.get("title", "") or "",
        content=content,
        lane=params.get("lane", "general"),
        tags=params.get("tags", []) or [],
    )
    return add_raw_memory(req)


def handle_obsidian_sync(params: dict) -> dict:
    """Obsidian 笔记同步：原文直存 + [[双链]] 实体/边沉淀（幂等）。"""
    from lantai.models.schemas import ObsidianSyncReq
    from lantai.services.obsidian_service import sync_obsidian_note
    content = params.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    return sync_obsidian_note(ObsidianSyncReq(
        title=params.get("title", "") or "",
        content=content,
        lane=params.get("lane", "general"),
        tags=params.get("tags", []) or [],
    ))


def handle_rollback(params: dict) -> dict:
    """回滚记忆到上一版本（Checkpoint 快照）。"""
    memory_id = params.get("memory_id", "")
    if not isinstance(memory_id, str) or not memory_id:
        raise ValueError("memory_id must be a non-empty string")
    from lantai.evolution.promoter import rollback as _rollback
    return _rollback(memory_id)


def handle_conflicts_list(params: dict) -> dict:
    """列出冲突账本事件（默认 open，等待人工裁决）。"""
    limit = params.get("limit", 50)
    status = params.get("status", "open")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    if not isinstance(status, str) or status not in ("open", "resolved", "dismissed", "all"):
        raise ValueError("status must be open/resolved/dismissed/all")
    from lantai.services.conflict_service import list_conflict_events
    return list_conflict_events(limit, status)


def handle_conflict_resolve(params: dict) -> dict:
    """裁决冲突事件：resolved（确认冲突成立）/ dismissed（误报）。"""
    event_id = params.get("event_id", "")
    decision = params.get("decision", "")
    note = params.get("note", "")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if decision not in ("resolved", "dismissed"):
        raise ValueError("decision must be 'resolved' or 'dismissed'")
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    from lantai.services.conflict_service import resolve_conflict_event
    return resolve_conflict_event(event_id, decision, note)



def handle_scene_get(params: dict) -> dict:
    """下钻场景：场景元数据 + 全部成员详情（渐进式披露第二步）。"""
    scene_id = params.get("scene_id", "")
    if not isinstance(scene_id, str) or not scene_id:
        raise ValueError("scene_id must be a non-empty string")
    from lantai.services.scene_service import get_scene
    return get_scene(scene_id)


def handle_scenes_list(params: dict) -> dict:
    """场景列表（heat 降序），供 Agent 浏览可用场景。"""
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from lantai.services.scene_service import list_scenes
    return list_scenes(limit)


def handle_recall_report(params: dict) -> dict:
    """零召回率监控报告：最近 N 天检索聚合（零召回率/按 lane/场景命中/token 估算）。"""
    days = params.get("days", None)
    if days is not None and (not isinstance(days, int) or isinstance(days, bool)
                             or not (1 <= days <= 365)):
        raise ValueError("days must be an int in [1, 365]")
    from lantai.observability.recall_report import recall_report
    return recall_report(days)
def handle_mem_help(params: dict) -> dict:
    """mem:help——返回支持的命令表与示例（纯函数）。"""
    from lantai.services.mem_command import mem_help
    return mem_help()


def handle_mem_sync(params: dict) -> dict:
    """mem:sync——刷新注入资产：scene 增量聚类补跑 + 今日 digest 重算。"""
    from lantai.services.mem_command import mem_sync
    return mem_sync()


def handle_mem_create_skill(params: dict) -> dict:
    """mem:create-skill——把会话主题沉淀为 Skill 资产（procedural 永不衰减）。"""
    from lantai.services.mem_command import create_skill
    name = params.get("name", "")
    description = params.get("description", "") or ""
    steps = params.get("steps", []) or []
    tags = params.get("tags", []) or []
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    if not all(isinstance(x, str) and x.strip() for x in steps):
        raise ValueError("steps must be a list of non-empty strings")
    return create_skill(name=name, description=description, steps=steps, tags=tags)


def handle_offload_read(params: dict) -> dict:
    """读取卸载全文：上下文只注入摘要 + 路径时，按 memory_id 取完整原文。"""
    memory_id = params.get("memory_id", "")
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id must be a non-empty string")
    from lantai.services.offload_service import read_offload_file
    return read_offload_file(memory_id)


def handle_wiki_read(params: dict) -> dict:
    """读取记忆 Wiki 页：先看 index/overview，再按 slug 下钻取页面正文。"""
    slug = params.get("slug", "")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug must be a non-empty string")
    from lantai.services.wiki_service import read_wiki_page
    return read_wiki_page(slug)


def handle_mem_recent(params: dict) -> dict:
    """最近记忆（只读）：按更新时间倒序列出 active 记忆。"""
    limit = params.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 200):
        raise ValueError("limit must be an int in [1, 200]")
    from lantai.services.memory_service import list_memories
    return list_memories(status="active", limit=limit)


def handle_mem_stats(params: dict) -> dict:
    """记忆概览（只读聚合）：总数/分布/待审候选/检查点/待审提案。"""
    from lantai.ops.overview import get_overview
    return get_overview()


def handle_mem_health(params: dict) -> dict:
    """深度健康检查：SQLite 可读 + 向量存储可用（不触发外部 LLM 调用）。"""
    checks: dict = {}
    try:
        from sqlmodel import func, select
        from lantai.models.tables import MemoryItem
        from lantai.storage import db
        with db.get_session() as s:
            checks["sqlite"] = "ok"
            checks["memory_count"] = int(
                s.exec(select(func.count()).select_from(MemoryItem)).one())
    except Exception as e:
        checks["sqlite"] = f"fail: {type(e).__name__}"
    try:
        from lantai.storage.vector_store import get_vector_store
        get_vector_store()
        checks["chromadb"] = "ok"
    except Exception as e:
        checks["chromadb"] = f"fail: {type(e).__name__}"
    checks["ok"] = checks.get("sqlite") == "ok" and checks.get("chromadb") == "ok"
    return checks


def handle_autodream_report(params: dict) -> dict:
    """蒸馏预演（dry-run 不写库）：聚类 → 规划，返回将产出的提案计划与跳过清单。"""
    limit = params.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)
                              or not (1 <= limit <= 5000)):
        raise ValueError("limit must be an int in [1, 5000]")
    from lantai.evolution.autodream import run_autodream_once
    return run_autodream_once(dry_run=True, limit=limit)


def handle_autodream_trigger(params: dict) -> dict:
    """执行一轮蒸馏：聚类 → 规划 → 落 pending 提案（宁 miss 不脏写：低置信度进 skipped，人工裁决后才应用）。"""
    from lantai.evolution.autodream import run_autodream_once
    return run_autodream_once(dry_run=False)


def handle_proposals_list(params: dict) -> dict:
    """待审提案列表（蒸馏/反射产出，等人工裁决）。"""
    status = params.get("status", "pending")
    limit = params.get("limit", 50)
    if not isinstance(status, str) or status not in (
            "pending", "approved", "rejected", "applied", "rolled_back"):
        raise ValueError("status must be one of: pending/approved/rejected/applied/rolled_back")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from lantai.services.evolution_service import list_proposals
    return list_proposals(status, limit)


def handle_proposal_decide(params: dict) -> dict:
    """裁决提案：approve 应用（先落 Checkpoint 可回滚），reject 归档并记 reason。"""
    proposal_id = params.get("proposal_id", "")
    approve = params.get("approve", False)
    reason = params.get("reason", "")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValueError("proposal_id must be a non-empty string")
    if not isinstance(approve, bool):
        raise ValueError("approve must be a boolean")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    from lantai.models.schemas import ProposalDecisionReq
    from lantai.services.evolution_service import decide_proposal
    return decide_proposal(proposal_id, ProposalDecisionReq(approve=approve, reason=reason))


def handle_tree_view(params: dict) -> dict:
    """分类树视图（只读）：节点 + 每节点挂载计数（v0.7 TreeMemory 窄版）。"""
    from lantai.services.tree_service import view_tree
    return view_tree()


def handle_tree_add(params: dict) -> dict:
    """新增分类树节点（父缺失/重名/非法名 -> 校验失败，宁 miss 不脏写）。"""
    name = params.get("name", "")
    parent_path = params.get("parent_path", "/")
    description = params.get("description", "") or ""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    from lantai.services.tree_service import add_tree_node
    return add_tree_node(name, parent_path, description)


def handle_tree_assign(params: dict) -> dict:
    """把记忆挂到分类树节点（节点/记忆必须存在）。"""
    memory_id = params.get("memory_id", "")
    node_path = params.get("node_path", "")
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id must be a non-empty string")
    if not isinstance(node_path, str) or not node_path.strip():
        raise ValueError("node_path must be a non-empty string")
    from lantai.services.tree_service import assign_memory_to_node
    return assign_memory_to_node(memory_id, node_path)


def handle_crystals_list(params: dict) -> dict:
    """结晶候选项列表（默认 candidate 待审）。"""
    status = params.get("status", "candidate")
    limit = params.get("limit", 50)
    if not isinstance(status, str) or status not in ("candidate", "approved", "archived"):
        raise ValueError("status must be one of: candidate/approved/archived")
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
        raise ValueError("limit must be an int in [1, 500]")
    from lantai.services.crystal_service import list_crystals
    return list_crystals(status, limit)


def handle_crystals_detect(params: dict) -> dict:
    """执行一轮结晶检测：聚类 -> 候选（dry_run=true 不写库；噪声 lane 排除）。"""
    dry_run = params.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ValueError("dry_run must be a boolean")
    from lantai.services.crystal_service import run_crystal_detect_once
    return run_crystal_detect_once(dry_run=dry_run)


def handle_crystal_decide(params: dict) -> dict:
    """裁决结晶候选：approve 必须带非空 steps -> 落成 Skill 资产；reject -> archived。"""
    crystal_id = params.get("crystal_id", "")
    approve = params.get("approve", False)
    steps = params.get("steps", []) or []
    reason = params.get("reason", "") or ""
    if not isinstance(crystal_id, str) or not crystal_id.strip():
        raise ValueError("crystal_id must be a non-empty string")
    if not isinstance(approve, bool):
        raise ValueError("approve must be a boolean")
    if not isinstance(steps, list) or not all(isinstance(x, str) for x in steps):
        raise ValueError("steps must be a list of strings")
    from lantai.services.crystal_service import decide_crystal
    return decide_crystal(crystal_id, approve, steps, reason)


def handle_reflect_run(params: dict) -> dict:
    """执行一轮反思：健康扫描 -> 提案 -> 裁决（高置信 auto-apply，中风险落 pending，宁 miss 不脏写）。"""
    from lantai.evolution.reflector import run_reflect_once
    return run_reflect_once(source="manual")


def handle_mem_usage(params: dict) -> dict:
    """用量统计（只读）：最近 N 天每日新增记忆数（缺日补零）。"""
    days = params.get("days", 7)
    if not isinstance(days, int) or isinstance(days, bool) or not (1 <= days <= 365):
        raise ValueError("days must be an int in [1, 365]")
    from lantai.ops.usage import collect_usage
    return collect_usage(days=days)


def handle_core_memory_get(params: dict) -> dict:
    """读取核心记忆块（只读）：identity/task/policy 持久块列表。"""
    namespace = params.get("namespace", "default")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("namespace must be a non-empty string")
    from lantai.services.memory_service import get_core_memory
    return get_core_memory(namespace)


def handle_verbatim_search(params: dict) -> dict:
    """原文直存检索（verbatim 专用通道）：原文默认不进混合召回，此通道可查（FTS+向量）。"""
    query = params.get("query", "")
    top_k = params.get("top_k", 5)
    if not isinstance(query, str) or not (1 <= len(query) <= 8000):
        raise ValueError("query must be a string of 1..8000 chars")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not (1 <= top_k <= 100):
        raise ValueError("top_k must be an int in [1, 100]")
    from lantai.retrieval.hybrid import hybrid_search
    return hybrid_search(query, top_k=top_k, memory_types=["verbatim"], use_rerank=False)


def handle_graph_view(params: dict) -> dict:
    """记忆关系星图（只读）：节点 + MemoryEdge 链接（supports/refines/contradicts/supersedes）+ 统计。"""
    from lantai.ops.graph import get_graph, validate_graph_limit
    limit = params.get("limit", 150)
    validate_graph_limit(limit)
    return get_graph(limit)

def handle_recall_chain(params: dict) -> dict:
    """记忆广播链（只读）：seed 记忆逐层触发关联记忆（烽燧相传）。"""
    from lantai.ops.recall_chain import build_recall_chain, validate_chain_params
    q = params.get("q", "")
    max_depth = params.get("max_depth", 3)
    branch = params.get("branch", 3)
    min_score = params.get("min_score", 0.3)
    total_max = params.get("total_max", 20)
    validate_chain_params(max_depth, branch, min_score, total_max)
    return build_recall_chain(q, max_depth, branch, min_score, total_max)


def handle_checkpoint_write(params: dict) -> dict:
    """底本：写入五段会话快照（ADR-0021）。"""
    from lantai.services.checkpoint_service import write_session_checkpoint
    session_id = params.get("session_id", "")
    blocks = params.get("blocks")
    if not isinstance(session_id, str) or len(session_id.strip()) < 3:
        raise ValueError("session_id must be a string of >= 3 chars")
    if not isinstance(blocks, dict):
        raise ValueError("blocks must be an object")
    return write_session_checkpoint(session_id, blocks)


def handle_checkpoint_latest(params: dict) -> dict:
    """底本：最近一次会话快照（只读）。"""
    from lantai.services.checkpoint_service import get_latest_checkpoint
    return get_latest_checkpoint()


def handle_persona_get(params: dict) -> dict:
    """器识：获取当前激活人格基座（只读）。"""
    from lantai.services.persona_service import get_active_persona, format_persona_context
    p = get_active_persona()
    if not p:
        return {"persona": None, "context": ""}
    return {"persona": p.model_dump(mode="json"), "context": format_persona_context(p)}


def handle_scratchpad_get(params: dict) -> dict:
    """札记：读取指定会话的工作区便签（ADR-0032）。"""
    session_id = str(params.get("session_id", "default") or "default")
    from lantai.services.scratchpad_service import get_scratchpad
    return {"session_id": session_id, "content": get_scratchpad(session_id)}


def handle_scratchpad_write(params: dict) -> dict:
    """札记：更新/覆盖指定会话的工作区便签（ADR-0032）。"""
    session_id = str(params.get("session_id", "default") or "default")
    content = str(params.get("content", "") or "")
    from lantai.services.scratchpad_service import write_scratchpad
    return write_scratchpad(session_id, content)



def handle_persona_set(params: dict) -> dict:
    """器识：设置或更新人格基座（L/G/E）。"""
    from lantai.services.persona_service import set_persona
    name = params.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    p = set_persona(
        name=name.strip(),
        linguistic_style=str(params.get("linguistic_style", "")),
        guidelines=str(params.get("guidelines", "")),
        epistemic_facts=str(params.get("epistemic_facts", "")),
        is_active=bool(params.get("is_active", True)),
    )
    return p.model_dump(mode="json")


TOOLS = {
    "search":   {"description": "搜索记忆", "inputSchema": {
        "type": "object", "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "top_k": {"type": "integer", "default": 5},
        }, "required": ["query"]}},
    "add":      {"description": "添加记忆（media_url 提供时走目识 vision：图片 -> 视觉描述作为正文，与 content 二选一）", "inputSchema": {
        "type": "object", "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "media_url": {"type": "string", "description": "目识（vision）：图片地址（http/https）或 data URI；提供时 content 必须为空"},
            "lane": {"type": "string", "default": "general"},
        }}},
    "feedback": {"description": "反馈记忆有用性", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string"},
            "query": {"type": "string"},
            "helped": {"type": "boolean"},
            "user_accepted": {"type": "boolean"},
        }, "required": ["memory_id"]}},
    "backfill": {"description": "回填弱标注：回答时实际用到的记忆 id 写回检索事件", "inputSchema": {
        "type": "object", "properties": {
            "event_id": {"type": "string", "description": "search 返回的检索事件 id"},
            "used_ids": {"type": "array", "items": {"type": "string"},
                         "description": "实际用进回答的记忆 id 列表"},
        }, "required": ["event_id", "used_ids"]}},
    "add_dialogue": {"description": "对话写通道：提交对话文本，自动提炼候选记忆（记住直通/闲聊入队）", "inputSchema": {
        "type": "object", "properties": {
            "text": {"type": "string", "description": "对话文本"},
            "user_id": {"type": "string", "default": "default"},
            "source": {"type": "string", "default": "dialogue"},
        }, "required": ["text"]}},
    "candidates_pending": {"description": "列出待审候选（被闸门拒绝、等人工裁决）", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}},
    "candidate_review": {"description": "审核候选：approve 仅生成 pending 提案（最终写入需再批准提案），reject 归档", "inputSchema": {
        "type": "object", "properties": {
            "candidate_id": {"type": "string"},
            "approve": {"type": "boolean"},
            "reason": {"type": "string", "default": ""},
        }, "required": ["candidate_id", "approve"]}},
    "candidate_refine": {"description": "披沙（精炼）：对指定候选记忆执行指代消解与结构化提纯（ADR-0030）", "inputSchema": {
        "type": "object", "properties": {
            "candidate_id": {"type": "string", "description": "候选记忆 ID"},
        }, "required": ["candidate_id"]}},
    "kaogong_eval": {"description": "考功（演化）：基于长程使用反馈与采纳率，对全库记忆执行功过升降级评定（ADR-0031）", "inputSchema": {
        "type": "object", "properties": {},
    }},
    "raw_add": {"description": "原文直存（verbatim）：内容直入 FTS5+向量，零 LLM", "inputSchema": {
        "type": "object", "properties": {
            "content": {"type": "string", "description": "原文内容（代码/日志/配置等）"},
            "title": {"type": "string", "default": ""},
            "lane": {"type": "string", "default": "general"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["content"]}},
    "obsidian_sync": {"description": "Obsidian 笔记同步：原文直存 + [[双链]] 实体/边沉淀（幂等）", "inputSchema": {
        "type": "object", "properties": {
            "title": {"type": "string", "default": ""},
            "content": {"type": "string", "description": "笔记正文"},
            "lane": {"type": "string", "default": "general"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["content"]}},
    "rollback": {"description": "回滚记忆到上一版本（Checkpoint 快照）", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string"},
        }, "required": ["memory_id"]}},
    "conflicts_list": {"description": "列出冲突账本事件（确定性规则命中记录）", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
            "status": {"type": "string", "default": "open"},
        }}},
    "conflict_resolve": {"description": "裁决冲突事件：resolved / dismissed", "inputSchema": {
        "type": "object", "properties": {
            "event_id": {"type": "string"},
            "decision": {"type": "string", "description": "resolved | dismissed"},
            "note": {"type": "string", "default": ""},
        }, "required": ["event_id", "decision"]}},
    "recall_report": {"description": "零召回率监控报告：最近 N 天检索聚合（零召回率/按 lane/场景命中/token 成本）", "inputSchema": {
        "type": "object", "properties": {
            "days": {"type": "integer", "default": 7},
        }}},
    "scene_get": {"description": "下钻场景：返回场景元数据与全部成员详情（渐进式披露）", "inputSchema": {
        "type": "object", "properties": {
            "scene_id": {"type": "string", "description": "场景 id（导航块或 scenes_list 中可见）"},
        }, "required": ["scene_id"]}},
    "scenes_list": {"description": "列出场景（按热度排序），供浏览可用场景", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 50},
        }}},
    "get_digest": {"description": "获取今日记忆盘点报告（摘要 + 五项统计）", "inputSchema": {
        "type": "object", "properties": {}}},
    "mem_help": {"description": "mem:help——返回支持的 mem: 命令表与示例（命令式维护入口）", "inputSchema": {
        "type": "object", "properties": {}}},
    "mem_sync": {"description": "mem:sync——刷新注入资产：场景增量聚类补跑 + 今日 digest 重算", "inputSchema": {
        "type": "object", "properties": {}}},
    "mem_create_skill": {"description": "mem:create-skill——沉淀 Skill 资产（名称 + 描述 + 步骤，procedural 永不衰减）", "inputSchema": {
        "type": "object", "properties": {
            "name": {"type": "string", "description": "Skill 名称（必填）"},
            "description": {"type": "string", "default": ""},
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "执行步骤列表（必填，非空）"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["name", "steps"]}},
    "offload_read": {"description": "读取卸载全文：长记忆经上下文卸载（摘要+路径注入）后，按 memory_id 取完整原文", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string", "description": "记忆 id（卸载注入行或 evidence 中可见）"},
        }, "required": ["memory_id"]}},
    "wiki_read": {"description": "读取记忆 Wiki 页：index/overview 列出的页面按 slug 下钻取正文（wikilink 下钻）", "inputSchema": {
        "type": "object", "properties": {
            "slug": {"type": "string", "description": "页面 slug（index.md 链接或 overview [[wikilink]] 中可见）"},
        }, "required": ["slug"]}},
    "mem_recent": {"description": "最近记忆（只读）：按更新时间倒序列出 active 记忆", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 20, "description": "返回条数 [1,200]"},
        }}},
    "mem_stats": {"description": "记忆概览（只读聚合）：总数/按 lane/decay_class/待审候选积压/检查点/待审提案", "inputSchema": {
        "type": "object", "properties": {}}},
    "mem_health": {"description": "深度健康检查：SQLite 可读 + 向量存储可用（不触发外部 LLM 调用）", "inputSchema": {
        "type": "object", "properties": {}}},
    "autodream_report": {"description": "蒸馏预演（dry-run 不写库）：聚类 → 规划，返回将产出的提案计划与跳过清单", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "description": "参与聚类的记忆上限（可选）"},
        }}},
    "autodream_trigger": {"description": "执行一轮蒸馏：聚类 → 规划 → 落 pending 提案（低置信度进 skipped，人工裁决后才应用）", "inputSchema": {
        "type": "object", "properties": {}}},
    "proposals_list": {"description": "待审提案列表（蒸馏/反射产出，等人工裁决）", "inputSchema": {
        "type": "object", "properties": {
            "status": {"type": "string", "default": "pending",
                       "description": "pending|approved|rejected|applied|rolled_back"},
            "limit": {"type": "integer", "default": 50},
        }}},
    "proposal_decide": {"description": "裁决提案：approve 应用（先落 Checkpoint 可回滚），reject 归档并记 reason", "inputSchema": {
        "type": "object", "properties": {
            "proposal_id": {"type": "string", "description": "提案 id（proposals_list 中可见）"},
            "approve": {"type": "boolean", "description": "true=应用；false=拒绝归档"},
            "reason": {"type": "string", "default": "", "description": "裁决理由（落库可审计）"},
        }, "required": ["proposal_id", "approve"]}},
    "tree_view": {"description": "分类树视图（只读）：节点 + 每节点挂载计数", "inputSchema": {
        "type": "object", "properties": {}}},
    "tree_add": {"description": "新增分类树节点（父缺失/重名/非法名 -> 校验失败，宁 miss 不脏写）", "inputSchema": {
        "type": "object", "properties": {
            "name": {"type": "string", "description": "节点名（不含路径分隔符）"},
            "parent_path": {"type": "string", "default": "/", "description": "父节点路径，如 /projects"},
            "description": {"type": "string", "default": ""},
        }, "required": ["name"]}},
    "tree_assign": {"description": "把记忆挂到分类树节点（节点/记忆必须存在）", "inputSchema": {
        "type": "object", "properties": {
            "memory_id": {"type": "string", "description": "记忆 id"},
            "node_path": {"type": "string", "description": "目标节点路径，如 /projects/release"},
        }, "required": ["memory_id", "node_path"]}},
    "crystals_list": {"description": "结晶候选项列表（默认 candidate 待审）", "inputSchema": {
        "type": "object", "properties": {
            "status": {"type": "string", "default": "candidate", "description": "candidate|approved|archived"},
            "limit": {"type": "integer", "default": 50},
        }}},
    "crystals_detect": {"description": "执行一轮结晶检测：聚类 -> 候选（dry_run=true 不写库；噪声 lane 排除）", "inputSchema": {
        "type": "object", "properties": {
            "dry_run": {"type": "boolean", "default": False, "description": "true=预演不写库"},
        }}},
    "crystal_decide": {"description": "裁决结晶候选：approve 必须带非空 steps -> 落成 Skill 资产；reject -> archived", "inputSchema": {
        "type": "object", "properties": {
            "crystal_id": {"type": "string", "description": "候选 id（crystals_list 中可见）"},
            "approve": {"type": "boolean", "description": "true=批准（需 steps）；false=拒绝归档"},
            "steps": {"type": "array", "items": {"type": "string"}, "description": "批准时必填：Skill 执行步骤"},
            "reason": {"type": "string", "default": ""},
        }, "required": ["crystal_id", "approve"]}},
    "reflect_run": {"description": "执行一轮反思：健康扫描 -> 提案 -> 裁决（高置信 auto-apply，中风险落 pending，宁 miss 不脏写）", "inputSchema": {
        "type": "object", "properties": {}}},
    "mem_usage": {"description": "用量统计（只读）：最近 N 天每日新增记忆数（缺日补零）", "inputSchema": {
        "type": "object", "properties": {
            "days": {"type": "integer", "default": 7, "description": "统计天数 [1,365]"},
        }}},
    "core_memory_get": {"description": "读取核心记忆块（只读）：identity/task/policy 持久块列表", "inputSchema": {
        "type": "object", "properties": {
            "namespace": {"type": "string", "default": "default"},
        }}},
    "verbatim_search": {"description": "原文直存检索（verbatim 专用通道）：原文默认不进混合召回，此通道可查（FTS+向量）", "inputSchema": {
        "type": "object", "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "top_k": {"type": "integer", "default": 5},
        }, "required": ["query"]}},
    "graph_view": {"description": "记忆关系星图（只读）：节点 + MemoryEdge 链接（supports/refines/contradicts/supersedes）+ lane/relation 统计", "inputSchema": {
        "type": "object", "properties": {
            "limit": {"type": "integer", "default": 150, "description": "节点上限 [1,500]"},
        }}},
    "recall_chain": {"description": "记忆广播链（烽燧，只读）：seed 记忆逐层触发关联记忆", "inputSchema": {
        "type": "object", "properties": {
            "q": {"type": "string", "description": "起点记忆/查询文本"},
            "max_depth": {"type": "integer", "default": 3, "description": "链深度 [1,5]"},
            "branch": {"type": "integer", "default": 3, "description": "每层分支数 [1,10]"},
            "min_score": {"type": "number", "default": 0.3, "description": "入选最低分数 [0,1]"},
            "total_max": {"type": "integer", "default": 20, "description": "链总记忆上限 [1,50]"},
        }, "required": ["q"]}},
    "checkpoint_write": {"description": "底本：写入五段会话快照（在做/下一步/工作区/决策/待办），下次会话启动注入", "inputSchema": {
        "type": "object", "properties": {
            "session_id": {"type": "string", "description": "会话标识（≥3 字符）"},
            "blocks": {"type": "object",
                       "description": "五段块：cp_active_intent/cp_next_action/cp_current_work/cp_key_decisions/cp_open_notes"},
        }, "required": ["session_id", "blocks"]}},
    "checkpoint_latest": {"description": "底本：读取最近一次会话快照（只读）", "inputSchema": {
        "type": "object", "properties": {}}},
    "persona_get": {"description": "器识：获取当前激活人格基座（L/G/E 三层与提示块，只读）", "inputSchema": {
        "type": "object", "properties": {}}},
    "persona_set": {"description": "器识：设置/更新人格基座（L/G/E 三层认知模型）", "inputSchema": {
        "type": "object", "properties": {
            "name": {"type": "string", "description": "人格名称"},
            "linguistic_style": {"type": "string", "description": "L: 言语与表达风格"},
            "guidelines": {"type": "string", "description": "G: 行为准则与戒律"},
            "epistemic_facts": {"type": "string", "description": "E: 认知底色与核心事实"},
            "is_active": {"type": "boolean", "default": True},
        }, "required": ["name"]}},
    "scratchpad_get": {"description": "札记：读取指定会话的工作区便签（ADR-0032，只读）", "inputSchema": {
        "type": "object", "properties": {
            "session_id": {"type": "string", "default": "default", "description": "会话标识"},
        }}},
    "scratchpad_write": {"description": "札记：更新/覆盖指定会话的工作区便签（ADR-0032，最大 1000 字符）", "inputSchema": {
        "type": "object", "properties": {
            "session_id": {"type": "string", "default": "default", "description": "会话标识"},
            "content": {"type": "string", "description": "要记下的即时便签文本"},
        }, "required": ["content"]}},

}

TOOL_HANDLERS = {
    "search": handle_search,
    "add": handle_add,
    "feedback": handle_feedback,
    "backfill": handle_backfill,
    "candidates_pending": handle_candidates_pending,
    "candidate_review": handle_candidate_review,
    "candidate_refine": handle_candidate_refine,
    "kaogong_eval": handle_kaogong_eval,
    "get_digest": handle_get_digest,
    "raw_add": handle_raw_add,
    "obsidian_sync": handle_obsidian_sync,
    "rollback": handle_rollback,
    "conflicts_list": handle_conflicts_list,
    "conflict_resolve": handle_conflict_resolve,
    "add_dialogue": handle_add_dialogue,
    "scene_get": handle_scene_get,
    "scenes_list": handle_scenes_list,
    "recall_report": handle_recall_report,
    "mem_help": handle_mem_help,
    "mem_sync": handle_mem_sync,
    "mem_create_skill": handle_mem_create_skill,
    "offload_read": handle_offload_read,
    "wiki_read": handle_wiki_read,
    "mem_recent": handle_mem_recent,
    "mem_stats": handle_mem_stats,
    "mem_health": handle_mem_health,
    "autodream_report": handle_autodream_report,
    "autodream_trigger": handle_autodream_trigger,
    "proposals_list": handle_proposals_list,
    "proposal_decide": handle_proposal_decide,
    "tree_view": handle_tree_view,
    "tree_add": handle_tree_add,
    "tree_assign": handle_tree_assign,
    "crystals_list": handle_crystals_list,
    "crystals_detect": handle_crystals_detect,
    "crystal_decide": handle_crystal_decide,
    "reflect_run": handle_reflect_run,
    "mem_usage": handle_mem_usage,
    "core_memory_get": handle_core_memory_get,
    "verbatim_search": handle_verbatim_search,
    "graph_view": handle_graph_view,
    "checkpoint_write": handle_checkpoint_write,
    "checkpoint_latest": handle_checkpoint_latest,
    "persona_get": handle_persona_get,
    "persona_set": handle_persona_set,
    "scratchpad_get": handle_scratchpad_get,
    "scratchpad_write": handle_scratchpad_write,
}


def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method", "")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lantai", "version": "0.16.0"}}}
    if method == "notifications/initialized":
        return None  # 通知无响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "tools": [{"name": n, **meta} for n, meta in TOOLS.items()]}}
    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        if not isinstance(params, dict) or not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "params/arguments must be objects"}}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        try:
            result = TOOL_HANDLERS[name](args)
        except (ValueError, ValidationError) as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": str(e)}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > 1_000_000:
            continue  # 超长行丢弃，防内存耗尽
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
