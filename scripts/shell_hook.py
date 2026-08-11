"""Shell Hook: pre_llm_call 时注入相关记忆（零依赖 CLI）。

契约：stdin JSON → stdout {context} 或 {}；2s 硬超时；异常静默降级。"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# ── 强制 UTF-8 I/O ──────────────────────────────────────────────
# Windows 默认 GBK 解码 stdin；Hermes 按 UTF-8 写 JSON，按 GBK 读则中文乱码
# （「你好」→「浣犲ソ」）→ query 检索零命中、注入静默失效。必须在读 stdin 前执行。
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lantai.core.settings import settings
from lantai.llm.client import embed
from lantai.storage.vector_store import get_vector_store
from lantai.storage import db
from lantai.models.tables import MemoryItem
from sqlmodel import select
from lantai.core.text import apply_recall_budget as _apply_recall_budget
from lantai.core.text import truncate_codepoints as _truncate_codepoints
from lantai.services.offload_service import build_offload_inject
from lantai.services.offload_service import write_offload_file

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
    "每轮对话中主动检索建议不超过 3 次；3 次仍无结果说明该信息不在记忆中，"
    "请直接根据已有信息回答。"
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


def _format_memory_entry(content: str, score: float,
                         max_chars: int, suffix: str) -> tuple[str, str]:
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
        return build_offload_inject(item.content, score, max_chars,
                                    _OFFLOAD_SUFFIX, path)
    except Exception:
        return _format_memory_entry(item.content, score, max_chars,
                                    _RECALL_TRUNCATION_SUFFIX)


def _is_skill_item(item) -> bool:
    """是否为可注入的 Skill 资产：procedural 衰减类 + 结构化步骤（Skill 资产化）。"""
    structure = item.structure or {}
    return item.decay_class == "procedural" and bool(structure.get("steps"))


def _format_skill_entry(item, score: float,
                        max_chars: int, suffix: str) -> tuple[str, str]:
    """Skill 资产注入块（纯函数）：名称 + 描述 + 编号步骤，非平铺文本。

    对应腾讯 Skill 资产的注入形态（名称/触发边界/步骤），兰台最小版：
    structure = {"name", "description", "steps"}。
    """
    structure = item.structure or {}
    steps = [s for s in (structure.get("steps") or [])
             if isinstance(s, str) and s.strip()]
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
    return build_scene_navigation_lines(
        items, per_scene_chars, _RECALL_TRUNCATION_SUFFIX)

def build_context(query: str) -> dict:
    """查询相关记忆，构建注入上下文。

    返回 {"context": ..., "event_id": ...}——event_id 供生成侧回填 used_ids
    （Hermes 若用 shell_hook 通道，回答后按注入的记忆 id 调 backfill）。
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
            _try_log(query, [], int((time.perf_counter() - t0) * 1000))
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
                            m, score, per_memory, _RECALL_TRUNCATION_SUFFIX)
                    elif len(m.content) > settings.SHELL_HOOK_OFFLOAD_CHARS:
                        # 上下文卸载（借鉴腾讯 offload）：全文落文件，注入摘要 + 路径
                        line, content = _format_offload_entry(m, score, per_memory)
                    else:
                        line, content = _format_memory_entry(
                            m.content, score, per_memory, _RECALL_TRUNCATION_SUFFIX)
                    entries.append((line, content, score, m.id))
                    break
        # scene 聚合层（ADR-0012）：命中记忆按场景分组，导航块优先注入（渐进式披露）
        scene_lines = []
        if settings.SCENE_LAYER_ENABLED:
            try:
                scene_lines = _build_scene_lines(
                    items, settings.SHELL_HOOK_MAX_CHARS_PER_SCENE)
            except Exception:
                scene_lines = []
        all_lines = scene_lines + [e[0] for e in entries]
        lines, _dropped = _apply_recall_budget(
            all_lines, settings.SHELL_HOOK_MAX_TOTAL_CHARS)
        detail_kept = max(0, len(lines) - len(scene_lines))
        evidence = [{"id": e[3], "content": e[1], "score": e[2]}
                    for e in entries[:detail_kept]]
        latency_ms = int((time.perf_counter() - t0) * 1000)
        event_id = _try_log(query, [{"score": 1.0 - r["distance"], "memory": {"id": r["id"]}}
                                    for r in results], latency_ms)
        out = {}
        if lines:
            memory_block = "\n".join(lines)
            out["context"] = memory_block
            if evidence:
                out["context"] = ("【本次依据】\n" +
                                  "\n".join(f"- ({e['id']}, score {e['score']}) {e['content']}"
                                             for e in evidence) +
                                  "\n\n【相关记忆】\n" + memory_block)
            out["evidence"] = evidence
            if settings.SHELL_HOOK_TOOLS_GUIDE:
                truncated = (_dropped > 0
                             or any(e["content"].endswith(_RECALL_TRUNCATION_SUFFIX)
                                    or e["content"].endswith(_OFFLOAD_SUFFIX)
                                    for e in evidence))
                out["context"] += "\n\n" + _build_tools_guide(truncated)
        if event_id:
            out["event_id"] = event_id
        return out
    except Exception:
        return {}


def _try_log(query: str, results: list, latency_ms: int) -> str | None:
    """Shell Hook 检索埋点（独立向量路径，方向二弱标注源）：失败零侵入。返回 event_id。"""
    try:
        from lantai.observability.retrieval_log import log_retrieval
        return log_retrieval(query, results, latency_ms=latency_ms,
                             trace_id="shell_hook")
    except Exception:
        return None


def _handle_dialogue(text: str) -> dict:
    """对话写入通道（v0.5）：复用常驻进程调 ingest_dialogue，异常零侵入。"""
    try:
        from lantai.ingestion.dialogue import ingest_dialogue
        return {"ok": True, **ingest_dialogue(text)}
    except Exception:
        return {}


def _handle_one(raw: str) -> dict:
    """处理单条请求（单发模式与 serve 模式共用）。"""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    # 对话写入通道（v0.5）：{"type": "dialogue", "text": ...}
    # 由 Hermes 插件 on_session_end flush 调用；LLM 提取需要更长超时。
    if data.get("type") == "dialogue":
        text = data.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return {}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_handle_dialogue, text)
            try:
                return future.result(timeout=settings.SHELL_HOOK_DIALOGUE_TIMEOUT)
            except FuturesTimeout:
                return {}
            except Exception:
                return {}

    query = data.get("query", "") or data.get("message", "") or data.get("prompt", "")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(build_context, query)
        try:
            return future.result(timeout=settings.SHELL_HOOK_TIMEOUT)
        except FuturesTimeout:
            return {}
        except Exception:
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
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return

    result = _handle_one(sys.stdin.read())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
