"""Shell Hook: pre_llm_call 时注入相关记忆（零依赖 CLI）。

契约：stdin JSON → stdout {context} 或 {}；2s 硬超时；异常静默降级。"""

import contextlib
import os
import sys

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认 GBK 解码 stdin；Hermes 按 UTF-8 写 JSON，按 GBK 读则中文乱码
# （「你好」→「浣犲ソ」）→ query 检索零命中、注入静默失效。必须在读 stdin 前执行。
# stderr 同钉（票 30 A5）：logger.exception 的异常 repr 嵌 GBK 外字符时产
# "--- Logging error ---" 噪声丢行。兜底 warnings.fail-loud（票 30 A5）：
# 旧版 os.environ.setdefault("PYTHONIOENCODING") 是死代码——该变量只在解释器
# 启动建流时被读，运行期 setdefault 对已建流编码毫无作用。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    import warnings

    warnings.warn(
        "stdio reconfigure failed; streams stay locale-encoded "
        "(PYTHONIOENCODING set at runtime has no effect on already-created streams)",
        stacklevel=2,
    )

# 幂等插入（票 .scratch/v0.66-next-window-2026-11/issues/01）：宿主 CLI 只 import 一次，
# 但测试/插件路径会对同一文件反复 exec_module——无条件 insert 让进程级 sys.path
# 无界增长（全量实测 +2574 条、收尾 2752 条重复项）。搜索语义不变：仓库根仍在
# sys.path 上（且保持首位优先），只是不再重复堆叠同一目录。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlmodel import col, select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.core.text import apply_recall_budget as _apply_recall_budget
from lantai.core.text import truncate_codepoints as _truncate_codepoints
from lantai.core.time import render_event_time
from lantai.gate.prefilter import relevance_check
from lantai.integrations.host_adapters import adapt_response
from lantai.integrations.host_protocol import (
    ACTION_BACKFILL,
    ACTION_CHECKPOINT,
    ACTION_CHECKPOINT_WRITE,
    ACTION_DIALOGUE,
    ACTION_QUERY,
    ACTION_VETO,
    parse_host_request,
    render_host_response,
)
from lantai.llm.client import embed
from lantai.llm.fence import fence_declaration, wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.retrieval.hierarchical import (
    rerank_by_information_density,
)
from lantai.retrieval.hybrid import hybrid_search

# 更正闸已下沉 lantai/retrieval/supersede.py（v0.68 票 02，行为零变化的移动重构）；
# 四个名字以同名别名保留：既有更正闸测试经 mod._apply_supersede_guard /
# mod._RETROSPECTIVE_RE / mod.settings 访问（tests/test_inject_supersede_guard.py）。
from lantai.retrieval.supersede import (
    _RETROSPECTIVE_RE,
    _apply_supersede_guard,
    _superseded_date_str,
    _superseded_marker,
)
from lantai.services.offload_service import build_offload_inject, write_offload_file
from lantai.storage import db
from lantai.storage.vector_store import get_vector_store

# ── 召回预算与工具指南（借鉴 TencentDB Agent Memory auto-recall）─────────────
# 单条记忆上限 + 总字符预算双控；按码点截断（不会切开 emoji 代理对）；
# 超预算截断/丢弃时在注入末尾附记忆使用指南（何时深挖、最多几次、如何回写）。
_RECALL_TRUNCATION_SUFFIX = "…（已截断；可用记忆工具查看详情）"
_PROFILE_TRUNCATION_SUFFIX = "…（user_profile.md 已截断；仅取前段）"
_PROFILE_MAX_CHARS = 4000
_OFFLOAD_SUFFIX = "…（已卸载全文；可调用 offload_read 工具查看）"
_RECALL_TOOLS_GUIDE_TRUNCATED = (
    "部分记忆片段已截断——若不足以回答，可主动触发记忆检索"
    "（例如说「查一下……」，或调用记忆 MCP 工具 search 获取更多详情；"
    "已卸载全文可调用 offload_read 查看）。"
)
_RECALL_TOOLS_GUIDE_RULES = (
    "每轮对话中主动检索建议不超过 3 次；3 次仍无结果说明该信息不在记忆中，请直接根据已有信息回答。"
)
_RECALL_TOOLS_GUIDE_WRITE = "对话中确认的新事实，可调用记忆 MCP 工具 add 保存为长期记忆。"


def _build_tools_guide(truncated: bool) -> str:
    """记忆使用指南：告诉 Agent 何时深挖、最多几次、如何回写。"""
    parts = ["【记忆使用指南】"]
    if truncated:
        parts.append("- " + _RECALL_TOOLS_GUIDE_TRUNCATED)
    parts.append("- " + _RECALL_TOOLS_GUIDE_RULES)
    parts.append("- " + _RECALL_TOOLS_GUIDE_WRITE)
    return "\n".join(parts)


def _live_atomic_facts(facts: list) -> list:
    """注入前来源存活断言（X-01 · P0-A 要求 3）：只放行来源记忆仍 active 的事实。

    读侧三处 JOIN 过滤（完璧 / 贯珠 / 探颐）是第一道闸；本函数是**注入前的最后一
    道闸**——上游任何通道（含未来新增的检索器）把已软删除记忆的原子事实递过来，
    都不进 prompt。`memory` 是软删除、`fact_atomic` 不级联，正文删不掉只能靠这里。

    口径与 `wanbi_retriever.fact_source_alive_clause()` 逐字一致：无来源事实放行；
    有来源事实要求来源行存在且 status == 'active'。
    **fail-closed**：来源查不动（异常）时一条都不注入——宁 miss 不脏写。
    """
    if not facts:
        return []
    sources = {
        f.get("source_memory_id")
        for f in facts
        if isinstance(f, dict) and f.get("source_memory_id")
    }
    if not sources:
        return [f for f in facts if isinstance(f, dict)]
    try:
        with db.get_session() as s:
            live = set(
                s.exec(
                    select(MemoryItem.id)
                    .where(col(MemoryItem.id).in_(sources))
                    .where(MemoryItem.status == "active")
                ).all()
            )
    except Exception as exc:
        logger.warning("完璧事实来源存活断言失败，放弃注入全部事实: %s", exc)
        return []
    kept = [
        f
        for f in facts
        if isinstance(f, dict)
        and (not f.get("source_memory_id") or f.get("source_memory_id") in live)
    ]
    dropped = len(facts) - len(kept)
    if dropped:
        logger.info("完璧事实注入断言：丢弃 %d 条来源已不存活的原子事实", dropped)
    return kept


def _format_memory_entry(
    content: str, score: float, max_chars: int, suffix: str, time_str: str = ""
) -> tuple[str, str]:
    """格式化单条记忆行 + 截断后内容（evidence 与注入行保持一致）。

    time_str 非空时行首带事件时间（`- [2026-09-20 | score 0.87] 正文`）——
    模型看得见「什么时候」（票据 .scratch/inject-frame-time/01，上游 f0.1 同款）；
    空串 = 无时间出身的旧行为逐字不变（`- [0.87] 正文`）。
    """
    truncated = _truncate_codepoints(content, max_chars, suffix)
    head = f"{time_str} | score {score}" if time_str else str(score)
    return f"- [{head}] {truncated}", truncated


def _event_time_str(item) -> str:
    """单条记忆的时间段渲染（粒度由 SHELL_HOOK_INJECT_DATE 决定；空串=不带）。"""
    return render_event_time(item, settings.SHELL_HOOK_INJECT_DATE, settings.SHELL_HOOK_INJECT_TZ)


def _format_offload_entry(item, score: float, max_chars: int) -> tuple[str, str]:
    """超长记忆 → 卸载注入（文件副作用；失败降级为截断注入）。

    对应腾讯 offload_server/compact 的窄版落点：上下文只注入摘要 + 全文路径，
    需要时经 MCP offload_read 取完整原文。
    """
    time_str = _event_time_str(item)
    try:
        path = write_offload_file(item.id, item.content)
        return build_offload_inject(
            item.content, score, max_chars, _OFFLOAD_SUFFIX, path, time_str=time_str
        )
    except Exception:
        return _format_memory_entry(
            item.content, score, max_chars, _RECALL_TRUNCATION_SUFFIX, time_str=time_str
        )


def _is_skill_item(item) -> bool:
    """是否为可注入的 Skill 资产：procedural 衰减类 + 结构化步骤（Skill 资产化）。"""
    structure = item.structure or {}
    return item.decay_class == "procedural" and bool(structure.get("steps"))


def _format_skill_entry(item, score: float, max_chars: int, suffix: str) -> tuple[str, str]:
    """Skill 资产注入块（纯函数）：名称 + 描述 + 编号步骤，非平铺文本。

    对应腾讯 Skill 资产的注入形态（名称/触发边界/步骤），兰台最小版：
    structure = {"name", "description", "steps"}。
    """
    structure = item.structure or {}
    steps = [s for s in (structure.get("steps") or []) if isinstance(s, str) and s.strip()]
    name = structure.get("name") or item.key or "技能"
    description = (structure.get("description") or item.content or "").strip()
    block = [f"## Skill: {name} (score {score})"]
    if description:
        block.append(f"- 描述: {description}")
    if steps:
        block.append("- 步骤:")
        block.extend(f"  {i}. {s.strip()}" for i, s in enumerate(steps, 1))
    text = "\n".join(block)
    truncated = _truncate_codepoints(text, max_chars, suffix)
    return truncated, truncated


def _build_resident_persona() -> str:
    """常驻个人画像与核心记忆（置顶无检索依赖，ADR-0029 器识 / Ticket 06）。

    读取优先级：
    1. user_profile.md 文件（LANTAI_HOME 或默认工作区/配置目录）
    2. PersonaProfile（器识 L/G/E 三层认知基座：言语风格/行为准则/认知底色）
    3. CoreMemoryBlock（核心记忆：identity / task / policy 块）
    无论检索有无命中，稳定作为置顶提示词注入。
    """
    from pathlib import Path

    sections = []
    # 1. 尝试探测 user_profile.md
    profile_paths = [
        Path(settings.LANTAI_HOME) / "user_profile.md" if settings.LANTAI_HOME else None,
        Path.home() / ".remembrance" / "user_profile.md",
        Path.home() / ".hermes" / "user_profile.md",
        Path(__file__).resolve().parent.parent / "user_profile.md",
    ]
    for p in profile_paths:
        if p and p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    content = _truncate_codepoints(
                        content, _PROFILE_MAX_CHARS, _PROFILE_TRUNCATION_SUFFIX
                    )
                    header = "=== 【用户偏好档案 (User Profile)】 ==="
                    footer = "====================================="
                    sections.append(wrap_as_data(f"{header}\n{content}\n{footer}"))
                    break
            except Exception:
                pass

    # 2. 器识·人格基座 (PersonaProfile L/G/E)
    try:
        from lantai.models.tables import PersonaProfile
        from lantai.services.persona_service import format_persona_context

        # 仅在明确存在激活人格配置或已配置画像时注入，避免未启用时干扰纯净单测
        with db.get_session() as s:
            active_p = s.exec(
                select(PersonaProfile).where(PersonaProfile.is_active == True)
            ).first()
            if active_p:
                p_ctx = format_persona_context(active_p)
                if p_ctx and p_ctx.strip():
                    sections.append(wrap_as_data(p_ctx.strip()))
    except Exception:
        pass

    # 3. 核心记忆块 (CoreMemoryBlock)
    try:
        from lantai.services.memory_service import get_core_memory

        cm = get_core_memory()
        blocks = cm.get("blocks") or []
        if blocks:
            lines = ["=== 【核心记忆 (Core Memory)】 ==="]
            for b in blocks:
                blk_name = (b.get("block") or "").upper()
                blk_content = (b.get("content") or "").strip()
                if blk_content:
                    lines.append(f"[{blk_name}]: {blk_content}")
            lines.append("================================")
            sections.append(wrap_as_data("\n".join(lines)))
    except Exception:
        pass

    return "\n\n".join(sections)


def _build_scene_lines(items, per_scene_chars: int) -> list[str]:
    """命中记忆按场景分组 → 导航块（渐进式披露；异常零侵入降级为空）。"""
    from lantai.services.scene_service import build_scene_navigation_lines

    return build_scene_navigation_lines(items, per_scene_chars, _RECALL_TRUNCATION_SUFFIX)


def build_context(query: str, session_id: str | None = None) -> dict:
    """查询相关记忆，构建注入上下文。

    包含：
    1. 个人常驻画像（Persona Profile + Core Memory + user_profile.md），置顶无检索依赖；
    2. 相关性闸门（relevance_check）过滤，低信度/纯闲聊跳过检索；
    3. 混合检索（hybrid_search，Vector + BM25 + FTS5），置信度下限过滤（SHELL_HOOK_SCORE_THRESHOLD）；
    4. 记忆使用指南与樊篱数据隔离。

    返回 {"context": ..., "event_id": ..., "evidence": ...}
    """
    resident_persona = _build_resident_persona()
    _decl = fence_declaration()

    def _persona_only() -> dict:
        if not resident_persona:
            return {}
        parts = [p for p in [_decl, resident_persona] if p]
        return {"context": "\n\n".join(parts)}

    if not query or len(query.strip()) <= settings.SHELL_HOOK_MIN_CHARS:
        return _persona_only()

    # 前置闸门检查（低信度/无信号闲聊跳过昂贵的检索计算）。
    # 票 04（P0-3）：判定必须带**会话身份**。闸门的「追问沿用」热缓存是进程级
    # 状态，此前不带身份 → 落进 `relevance_check` 的无身份全局桶 `_LAST_GATE_DECISION`；
    # 常驻 serve 进程里 A 会话一次带信号的查询，会让 B 会话的无关短消息
    # （如「你先看看」，自身判 no_signal）继承 needs_memory=True——实测白跑昂贵
    # 检索并落一条 zero_result 的 RetrievalEvent，把别人的判定噪声带进无关对话。
    # `session_id` 本来就在调用处手里；无 session 的调用方（桌面/CLI 单用户）
    # 仍落 "default" 桶，行为零漂移。
    gate_decision = relevance_check(query, user_id=session_id or "default")
    if not gate_decision.get("needs_memory", False):
        return _persona_only()

    import time

    try:
        t0 = time.perf_counter()
        # D-04 预算传导（票 `.scratch/v0.65-next-window-2026-11/01`）：
        # 剩余预算传给检索链——embed/rerank 在预算内快速失败走降级路径，
        # 而非挂满 30s/600s 撞外层 _run_with_timeout 墙丢整段注入。
        # **预留 15% 调度余量**：embed 睡满预算后还要走 BM25/FTS 兜底 +
        # 组装 + 置顶拼接，外层 join(timeout) 是硬墙——不给余量必擦线超
        # （实测 2.0 预算全给 embed → 总 2.17s > 2.0 墙，降级结果被墙丢弃）。
        budget_s = max(0.05, settings.SHELL_HOOK_TIMEOUT * 0.85 - (time.perf_counter() - t0))
        raw_results = hybrid_search(query, top_k=settings.SHELL_HOOK_TOP_K, budget_s=budget_s)

        # 相似度置信度阈值过滤：D-03 分路阈值（reranker 走 0.5 语义门禁，RRF 走 0.15 独立量纲，避免第 2/3 名被一刀切）
        def _score_passes(r: dict) -> bool:
            score = r.get("score", 0.0)
            mode = r.get("score_mode", "rrf")
            thresh = (
                settings.SHELL_HOOK_SCORE_THRESHOLD
                if mode == "reranker"
                else getattr(settings, "SHELL_HOOK_SCORE_THRESHOLD_RRF", 0.15)
            )
            return score >= thresh

        results = [r for r in (raw_results or []) if isinstance(r, dict) and _score_passes(r)]
        if not results:
            # v0.68 票 01：零命中也把已落库事件的 event_id 带出——DLP 否决零命中轮时
            # 插件才构造得出 retrieval_veto 帧（ADR-0084 否决链只认 event_id）。
            # 对齐 REST routes_search / MCP handle_search 零命中分支的既有口径；
            # request_id 此分支本就没生成，如实不带。
            zero_event_id = _try_log(
                query, [], int((time.perf_counter() - t0) * 1000), session_id=session_id
            )
            out = _persona_only()
            if zero_event_id:
                out["event_id"] = zero_event_id
            return out

        # 磨勘（v0.29 · MCMA 建议项 6）：信息密度优先重排与条目硬顶（<= SHELL_HOOK_MAX_INJECT_ITEMS，默认 5）
        max_items = getattr(settings, "SHELL_HOOK_MAX_INJECT_ITEMS", 5)
        results = rerank_by_information_density(results, max_items=max_items)
        # 更正闸（周报 11-03 P0-2）：放在硬顶之后——只有新值确实会被注入时才丢旧值，
        # 新值被硬顶挤掉时旧值留下并带「已更正」标记，不会两条都不见
        results = _apply_supersede_guard(results, query)

        items = []
        for r in results:
            mem_dict = r.get("memory")
            if isinstance(mem_dict, dict):
                with contextlib.suppress(Exception):
                    items.append(MemoryItem.model_validate(mem_dict))

        per_memory = settings.SHELL_HOOK_MAX_CHARS_PER_MEMORY
        entries = []  # (注入行, evidence 内容, score, id)——顺序与 results 一致
        for r in results:
            r_mem = r.get("memory") or {}
            m_id = r_mem.get("id") or r.get("id")
            score = round(float(r.get("score", 0.0)), 2)

            # 若结果项包含完璧 FactAtomic 原子事实列表，则优先连同事实结构保整注入
            atomic_facts = _live_atomic_facts(r.get("atomic_facts") or [])
            atomic_suffix = ""
            if atomic_facts:
                fact_stmts = [
                    f["statement"]
                    for f in atomic_facts
                    if isinstance(f, dict) and f.get("statement")
                ]
                if fact_stmts:
                    atomic_suffix = f" [完璧事实: {'；'.join(fact_stmts)}]"

            matched_item = None
            for m in items:
                if m.id == m_id:
                    matched_item = m
                    break

            if matched_item:
                m = matched_item
                if _is_skill_item(m):
                    line, content = _format_skill_entry(
                        m, score, per_memory, _RECALL_TRUNCATION_SUFFIX
                    )
                elif len(m.content) > settings.SHELL_HOOK_OFFLOAD_CHARS:
                    # 上下文卸载（借鉴腾讯 offload）：全文落文件，注入摘要 + 路径
                    line, content = _format_offload_entry(m, score, per_memory)
                else:
                    line, content = _format_memory_entry(
                        m.content,
                        score,
                        per_memory,
                        _RECALL_TRUNCATION_SUFFIX,
                        time_str=_event_time_str(m),
                    )
                target_id = m.id
            else:
                # 针对无直接对应 MemoryItem 实例但属于检索合法候选的条目（如纯事实块）
                doc_text = (
                    r.get("document")
                    or (r_mem.get("content") if isinstance(r_mem, dict) else "")
                    or str(r)
                )
                line, content = _format_memory_entry(
                    doc_text, score, per_memory, _RECALL_TRUNCATION_SUFFIX
                )
                target_id = m_id

            if atomic_suffix:
                line = f"{line}{atomic_suffix}"
                content = f"{content}{atomic_suffix}"
            entries.append((line, content, score, target_id))

        # scene 聚合层（ADR-0012）：命中记忆按场景分组，导航块优先注入（渐进式披露）
        scene_lines = []
        if settings.SCENE_LAYER_ENABLED:
            try:
                scene_lines = _build_scene_lines(items, settings.SHELL_HOOK_MAX_CHARS_PER_SCENE)
            except Exception:
                scene_lines = []
        all_lines = scene_lines + [e[0] for e in entries]
        lines, _dropped = _apply_recall_budget(all_lines, settings.SHELL_HOOK_MAX_TOTAL_CHARS)
        detail_kept = max(0, len(lines) - len(scene_lines))
        evidence = [{"id": e[3], "content": e[1], "score": e[2]} for e in entries[:detail_kept]]
        latency_ms = int((time.perf_counter() - t0) * 1000)
        request_id = new_id("req")  # 回执链（ADR-0049）：一次注入调用的整体标识
        event_id = _try_log(
            query,
            [
                {"score": r.get("score", 0.0), "memory": {"id": (r.get("memory") or {}).get("id")}}
                for r in results
            ],
            latency_ms,
            session_id=session_id,
            request_id=request_id,
        )
        out = {}
        if lines:
            # 樊篱（P0 票03）：注入给宿主 LLM 的记忆正文是数据不是指令——
            # 依据段与记忆段各自围栏，正文逃逸标记已中性化
            memory_block = wrap_as_data("\n".join(lines))
            out["context"] = memory_block
            if evidence:
                out["context"] = (
                    "【本次依据】\n"
                    + wrap_as_data(
                        "\n".join(
                            f"- ({e['id']}, score {e['score']}) {e['content']}" for e in evidence
                        )
                    )
                    + "\n\n【相关记忆】\n"
                    + memory_block
                )
            out["evidence"] = evidence
            if settings.SHELL_HOOK_TOOLS_GUIDE:
                truncated = _dropped > 0 or any(
                    e["content"].endswith(_RECALL_TRUNCATION_SUFFIX)
                    or e["content"].endswith(_OFFLOAD_SUFFIX)
                    for e in evidence
                )
                out["context"] += "\n\n" + _build_tools_guide(truncated)

        # 最终组装：声明 → 常驻画像 → 检索记忆（声明先于所有数据段）
        parts = [p for p in [_decl, resident_persona, out.get("context")] if p]
        out["context"] = "\n\n".join(parts) if parts else ""

        if event_id:
            out["event_id"] = event_id
        if event_id:
            out["request_id"] = request_id  # 宿主回执时按 request_id 对账（ADR-0049）
        return out
    except Exception:
        return _persona_only()


def _try_log(
    query: str,
    results: list,
    latency_ms: int,
    session_id: str | None = None,
    request_id: str | None = None,
) -> str | None:
    """Shell Hook 检索埋点（独立向量路径，方向二弱标注源）：失败零侵入。返回 event_id。"""
    try:
        from lantai.observability.retrieval_log import log_retrieval

        return log_retrieval(
            query,
            results,
            latency_ms=latency_ms,
            trace_id="shell_hook",
            session_id=session_id,
            request_id=request_id,
        )
    except Exception:
        return None


def _handle_dialogue(text: str, session_id: str = "", turn: int | None = None) -> dict:
    """对话写入通道（v0.5）：复用常驻进程调 ingest_dialogue，异常零侵入。

    session_id/turn：来源链透传（P0 票02）——落候选 session_id 列与
    provenance.origin_turn，随演化链继承到记忆。
    """
    try:
        from lantai.ingestion.dialogue import ingest_dialogue

        return {
            "ok": True,
            **ingest_dialogue(text, session_id=session_id or "", turn=turn),
        }
    except Exception:
        return {}


def _handle_backfill(event_id: str, used_ids, request_id: str | None = None) -> dict:
    """注入回执通道（P0 票02）：插件注入记忆后按 event_id 回填 used_ids（弱标注）。

    空 used_ids 静默拒绝：backfill_used_ids 是整体覆盖语义，空表回执会抹掉
    既有弱标注（审查整改）。
    """
    if not isinstance(event_id, str) or not event_id:
        return {}
    if not isinstance(used_ids, list) or not used_ids:
        return {}
    if not all(isinstance(x, str) for x in used_ids):
        return {}
    try:
        from lantai.observability.retrieval_log import backfill_used_ids

        backfill_used_ids(event_id, used_ids, request_id=request_id)
        return {
            "ok": True,
            "event_id": event_id,
            "used_count": len(used_ids),
            "receipt_status": "acked",
        }
    except Exception:
        return {}


def _handle_veto(event_id: str) -> dict:
    """检索事件否决通道（ADR-0084，v0.67 票 01）：宿主否决一轮 → 作废该检索事件。

    抹原话/清 used_ids/置 vetoed 终态（服务端 `mark_vetoed`）。ok=True 一律返回
    （帧本身合法送达）；`vetoed=False` 表示幂等重放或事件不存在——如实上报，
    不编造成功。异常静默降级 `{}`（守门通道不得拖垮 serve 循环）。
    """
    if not isinstance(event_id, str) or not event_id.strip():
        return {}
    try:
        from lantai.observability.retrieval_log import mark_vetoed

        return {"ok": True, "event_id": event_id, "vetoed": bool(mark_vetoed(event_id))}
    except Exception:
        return {}


def _handle_checkpoint() -> dict:
    """底本注入通道（ADR-0022）：会话启动读取上次会话五段快照。

    独立于检索预算（非每轮召回）；无快照/异常零侵入返回空。
    """
    try:
        from lantai.services.checkpoint_service import inject_checkpoint_context

        text = inject_checkpoint_context()
        return {"context": text} if text else {}
    except Exception:
        return {}


def _handle_checkpoint_write(session_id: str, blocks: dict) -> dict:
    """底本写入通道（ADR-0022）：插件 on_session_end 落五段快照（同库同语义）。"""
    try:
        from lantai.services.checkpoint_service import write_session_checkpoint

        return write_session_checkpoint(session_id, blocks)
    except Exception:
        return {}


def _run_with_timeout(func, timeout, *args, _fallback=None, _on_abandon=None):
    """超时包装（D-04，票 `.scratch/v0.65-next-window-2026-11/01`）。

    超时**与执行异常**都返回 `_fallback()`（降级工厂，零参回调——QUERY 通道传
    常驻画像，免得超时/抛错把画像段一并丢弃）；未传工厂时保持原行为返回 `{}`。
    正常返回路径不碰工厂。daemon 线程不可取消是 Python 线程模型限制，预算控制在
    build_context 内传导（见其 docstring）。

    `_on_abandon(result)`（巡检 2026-11-07 12:00）：主线程超时放弃等待后，worker
    仍会把 func 跑完——它的返回值没人收，但它的副作用（QUERY 通道里 `_try_log`
    落的 RetrievalEvent，带原话）已经发生，且宿主手里没有 event_id，回执与
    retrieval_veto 都够不着。worker 跑完时若发现已被放弃，就在 worker 线程里把
    迟到的结果交给 `_on_abandon` 收尾。「完成」与「放弃」在同一把锁下二选一：
    worker 先完成 → 主线程照常拿结果（含 event_id）；主线程先放弃 → worker
    必定走收尾。没有两边都不管的窗口。回调异常只记日志，不影响 serve 循环。
    """
    import threading

    handoff = threading.Lock()

    class TaskThread(threading.Thread):
        def __init__(self):
            super().__init__()
            self.result = {}
            self.exc: Exception | None = None
            self.daemon = True
            self.finished = False  # handoff 锁下置位：结果已可交给主线程
            self.abandoned = False  # handoff 锁下置位：主线程已放弃等待

        def run(self):
            result = None
            try:
                result = func(*args)
            except Exception as exc:
                # 线程内异常不会传播回主线程；此前被 `contextlib.suppress` 直接吞掉，
                # 结果停在 `{}`——票 04 要求把它记下来，交给主线程降级。
                self.exc = exc
            with handoff:
                if not self.abandoned:
                    if self.exc is None:
                        self.result = result
                    self.finished = True
                    return
            # 主线程已放弃：迟到的结果没人收，副作用交给收尾回调
            if _on_abandon is not None and self.exc is None:
                try:
                    _on_abandon(result)
                except Exception:
                    logger.exception("shell_hook 超时收尾回调失败")

    def _degrade(reason: str) -> dict:
        """降级出口：留痕 + 常驻画像兜底（无工厂/工厂自身失败时才空返回）。

        留痕用模块顶部的 `logger`（stderr）——降级路径上绝不做 import：被掐掉的
        worker 线程此刻正在首次 import jieba/chromadb（冷启动 0.9–1.4s），主线程
        若在这里触发一次 import，会撞 import 锁把 hook 预算整个吃掉（实测
        0.2s 的墙被拖到 1.8s，把超时预算用例拖红）。
        """
        logger.warning("shell_hook %s，按降级兜底（画像仍在，检索段可能缺失）", reason)
        if _fallback is None:
            return {}
        try:
            return _fallback()
        except Exception:
            logger.exception("shell_hook 降级工厂自身失败")
            return {}

    t = TaskThread()
    t.start()
    t.join(timeout)
    with handoff:
        if not t.finished:
            t.abandoned = True
    if t.abandoned:
        return _degrade(f"超时（{timeout}s）")
    if t.exc is not None:
        # 票 04（P0-3）：异常路径同样不得丢常驻画像。此前只有「线程还活着（超时）」
        # 才走 _fallback，而 worker 里的异常把 result 留在 `{}` 且不留任何日志；
        # `build_context` 的内部 try 只兜住检索段，闸门调用与收尾拼串都在它之外，
        # 那一段任何抛错都会让 Hermes 拿到空上下文（画像静默消失、且完全不可观测）。
        return _degrade(f"执行异常（{t.exc!r}）")
    return t.result


class HookAuthError(RuntimeError):
    pass


def assert_hook_api_key_configured() -> None:
    if not settings.API_KEY:
        raise HookAuthError("shell hook 拒绝运行:未配置 API_KEY(禁止 DEV MODE 回退)")


def _handle_one(raw: str) -> dict:
    """解析单个输入，返回字典结果（宿主适配后的最终输出帧）。

    分层（票 06）：
    - 协议解析/字段校验 → `lantai.integrations.host_protocol`（宿主无关归一化层）
    - 分发到 handler + 超时包装 → 本文件的 `_dispatch_one`
    - 响应帧按宿主翻译 → `lantai.integrations.host_adapters`

    宿主由环境变量 `LANTAI_HOST` 指定（宿主调用钩子时设置）；缺省 = 直通兰台
    自有形状，故既有断言零改动即证明等价。
    """
    assert_hook_api_key_configured()
    return adapt_response(os.environ.get("LANTAI_HOST") or None, _dispatch_one(raw))


def _dispatch_one(raw: str) -> dict:
    """归一化 → 分发到 handler + 超时包装（宿主无关）。"""
    req = parse_host_request(raw)
    if req is None:
        return {}

    if req.action == ACTION_DIALOGUE:
        return _run_with_timeout(
            _handle_dialogue,
            settings.SHELL_HOOK_DIALOGUE_TIMEOUT,
            req.text,
            req.session_id,
            req.turn,
        )

    if req.action == ACTION_BACKFILL:
        return _run_with_timeout(
            _handle_backfill,
            settings.SHELL_HOOK_TIMEOUT,
            req.event_id,
            list(req.used_ids),
            req.request_id,
        )

    if req.action == ACTION_VETO:
        return _run_with_timeout(_handle_veto, settings.SHELL_HOOK_TIMEOUT, req.event_id)

    if req.action == ACTION_CHECKPOINT:
        return _run_with_timeout(_handle_checkpoint, settings.SHELL_HOOK_TIMEOUT)

    if req.action == ACTION_CHECKPOINT_WRITE:
        return _run_with_timeout(
            _handle_checkpoint_write,
            settings.SHELL_HOOK_TIMEOUT,
            req.session_id,
            req.blocks,
        )

    if req.action == ACTION_QUERY:
        # D-04（票 `.scratch/v0.65-next-window-2026-11/01`）：超时兜底返回
        # 常驻画像——检索超时不该把"置顶无检索依赖"的画像段一并丢弃。
        def _query_fallback() -> dict:
            p = _build_resident_persona()
            if not p:
                return {}
            decl = fence_declaration()
            parts = [x for x in [decl, p] if x]
            return {"context": "\n\n".join(parts)}

        # 巡检 2026-11-07 12:00：超时后 worker 仍会跑完并落一条带原话的检索事件，
        # 而兜底响应不带 event_id——宿主既回执不了、也否决不了。就地抹原话置 missed。
        def _query_abandoned(late: dict) -> None:
            event_id = (late or {}).get("event_id") if isinstance(late, dict) else None
            if not event_id:
                return
            from lantai.observability.retrieval_log import mark_abandoned

            if mark_abandoned(event_id):
                logger.info("shell_hook 超时轮检索事件已收尾（抹原话、置 missed）: %s", event_id)

        return _run_with_timeout(
            build_context,
            settings.SHELL_HOOK_TIMEOUT,
            req.query,
            req.session_id,
            _fallback=_query_fallback,
            _on_abandon=_query_abandoned,
        )

    return {}


def _prewarm() -> None:
    """启动即初始化向量库连接与 jieba（best-effort，失败不阻断启动）。

    首帧预算 SHELL_HOOK_TIMEOUT 仅 2s，而向量库客户端冷启动（Chroma
    client/collection 首次创建）实测约 2.2s——留在首帧的 build_context
    里才初始化，首帧必然超时返回空注入。常驻进程的本意就是消除冷启动
    （见 main 的 serve 注释）；启动时提前建连，把该项成本移出首帧预算。
    jieba 同理（D-04，票 `.scratch/v0.65-next-window-2026-11/01`）：完璧
    事实检索（wanbi_retriever）首次分词触发模型加载实测 ~0.9-1.4s，同样
    吃首帧预算——启动时预热一次，常驻后零成本。
    失败降级交由帧内既有异常零侵入路径逐帧处理。
    """
    with contextlib.suppress(Exception):
        get_vector_store()
    with contextlib.suppress(Exception):
        import jieba

        jieba.lcut("预热")


def main():
    _prewarm()
    if "--serve" in sys.argv:
        # 守护模式：NDJSON 循环，每行一个请求 → 每行一个响应。
        # 常驻进程消除冷启动开销（chromadb/jieba 只加载一次），
        # 供插件通道热调用（serve/桌面模式 Hermes 不跑 shell hooks）。
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            result = _handle_one(line)
            sys.stdout.write(render_host_response(result) + "\n")
            sys.stdout.flush()
        return

    result = _handle_one(sys.stdin.read())
    print(render_host_response(result))


if __name__ == "__main__":
    main()
