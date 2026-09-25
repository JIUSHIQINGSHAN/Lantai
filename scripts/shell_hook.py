"""Shell Hook: pre_llm_call 时注入相关记忆（零依赖 CLI）。

契约：stdin JSON → stdout {context} 或 {}；2s 硬超时；异常静默降级。"""

import os
import sys

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认 GBK 解码 stdin；Hermes 按 UTF-8 写 JSON，按 GBK 读则中文乱码
# （「你好」→「浣犲ソ」）→ query 检索零命中、注入静默失效。必须在读 stdin 前执行。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.text import apply_recall_budget as _apply_recall_budget
from lantai.core.text import truncate_codepoints as _truncate_codepoints
from lantai.integrations.host_adapters import adapt_response
from lantai.integrations.host_protocol import (
    ACTION_BACKFILL,
    ACTION_CHECKPOINT,
    ACTION_CHECKPOINT_WRITE,
    ACTION_DIALOGUE,
    ACTION_QUERY,
    parse_host_request,
    render_host_response,
)
from lantai.llm.client import embed
from lantai.llm.fence import fence_declaration, wrap_as_data
from lantai.models.tables import MemoryItem
from lantai.services.offload_service import build_offload_inject, write_offload_file
from lantai.storage import db
from lantai.storage.vector_store import get_vector_store

# ── 召回预算与工具指南（借鉴 TencentDB Agent Memory auto-recall）─────────────
# 单条记忆上限 + 总字符预算双控；按码点截断（不会切开 emoji 代理对）；
# 超预算截断/丢弃时在注入末尾附记忆使用指南（何时深挖、最多几次、如何回写）。
_RECALL_TRUNCATION_SUFFIX = "…（已截断；可用记忆工具查看详情）"
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


def _format_memory_entry(
    content: str, score: float, max_chars: int, suffix: str
) -> tuple[str, str]:
    """格式化单条记忆行 + 截断后内容（evidence 与注入行保持一致）。"""
    truncated = _truncate_codepoints(content, max_chars, suffix)
    return f"- [{score}] {truncated}", truncated


def _format_offload_entry(item, score: float, max_chars: int) -> tuple[str, str]:
    """超长记忆 → 卸载注入（文件副作用；失败降级为截断注入）。

    对应腾讯 offload_server/compact 的窄版落点：上下文只注入摘要 + 全文路径，
    需要时经 MCP offload_read 取完整原文。
    """
    try:
        path = write_offload_file(item.id, item.content)
        return build_offload_inject(item.content, score, max_chars, _OFFLOAD_SUFFIX, path)
    except Exception:
        return _format_memory_entry(item.content, score, max_chars, _RECALL_TRUNCATION_SUFFIX)


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


def _build_scene_lines(items, per_scene_chars: int) -> list[str]:
    """命中记忆按场景分组 → 导航块（渐进式披露；异常零侵入降级为空）。"""
    from lantai.services.scene_service import build_scene_navigation_lines

    return build_scene_navigation_lines(items, per_scene_chars, _RECALL_TRUNCATION_SUFFIX)


def build_context(query: str, session_id: str | None = None) -> dict:
    """查询相关记忆，构建注入上下文。

    返回 {"context": ..., "event_id": ...}——event_id 供生成侧回填 used_ids
    （Hermes 若用 shell_hook 通道，回答后按注入的记忆 id 调 backfill）。

    session_id：来源链透传（P0 票02）——落检索事件 session_id 列，
    写线活性判据「带 session 的读才算真实会话读」依赖此值。
    """
    if not query or len(query.strip()) <= settings.SHELL_HOOK_MIN_CHARS:
        return {}

    import time

    try:
        t0 = time.perf_counter()
        qv = embed([query])[0]
        store = get_vector_store()
        results = store.search(qv, top_k=settings.SHELL_HOOK_TOP_K)
        if not results:
            _try_log(query, [], int((time.perf_counter() - t0) * 1000), session_id=session_id)
            return {}

        ids = [r["id"] for r in results]
        with db.get_session() as s:
            items = s.exec(
                select(MemoryItem)
                .where(MemoryItem.id.in_(ids))
                .where(MemoryItem.status == "active")
            ).all()

        per_memory = settings.SHELL_HOOK_MAX_CHARS_PER_MEMORY
        entries = []  # (注入行, evidence 内容, score, id)——顺序与 results 一致
        for r in results:
            for m in items:
                if m.id == r["id"]:
                    score = round(1.0 - r["distance"], 2)
                    if _is_skill_item(m):
                        line, content = _format_skill_entry(
                            m, score, per_memory, _RECALL_TRUNCATION_SUFFIX
                        )
                    elif len(m.content) > settings.SHELL_HOOK_OFFLOAD_CHARS:
                        # 上下文卸载（借鉴腾讯 offload）：全文落文件，注入摘要 + 路径
                        line, content = _format_offload_entry(m, score, per_memory)
                    else:
                        line, content = _format_memory_entry(
                            m.content, score, per_memory, _RECALL_TRUNCATION_SUFFIX
                        )
                    entries.append((line, content, score, m.id))
                    break
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
            [{"score": 1.0 - r["distance"], "memory": {"id": r["id"]}} for r in results],
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
            decl = fence_declaration()
            if decl:
                out["context"] = decl + "\n" + out["context"]
        if event_id:
            out["event_id"] = event_id
        if event_id:
            out["request_id"] = request_id  # 宿主回执时按 request_id 对账（ADR-0049）
        return out
    except Exception:
        return {}


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


def _run_with_timeout(func, timeout, *args):
    import threading

    class TaskThread(threading.Thread):
        def __init__(self):
            super().__init__()
            self.result = {}
            self.daemon = True

        def run(self):
            import contextlib

            with contextlib.suppress(Exception):
                self.result = func(*args)

    t = TaskThread()
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {}
    return t.result


def _handle_one(raw: str) -> dict:
    """解析单个输入，返回字典结果（宿主适配后的最终输出帧）。

    分层（票 06）：
    - 协议解析/字段校验 → `lantai.integrations.host_protocol`（宿主无关归一化层）
    - 分发到 handler + 超时包装 → 本文件的 `_dispatch_one`
    - 响应帧按宿主翻译 → `lantai.integrations.host_adapters`

    宿主由环境变量 `LANTAI_HOST` 指定（宿主调用钩子时设置）；缺省 = 直通兰台
    自有形状，故既有断言零改动即证明等价。
    """
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
        return _run_with_timeout(
            build_context, settings.SHELL_HOOK_TIMEOUT, req.query, req.session_id
        )

    return {}


def main():
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
