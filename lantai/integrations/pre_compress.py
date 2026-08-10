"""
压缩前抢救（改进四）：Hermes 上下文压缩前把最近对话快照异步写入记忆库。

设计：
- _render 为纯函数（便于单测）：抽取最近 N 条 user/assistant 非空文本，逐条截断。
- flush_before_compress 启动 daemon 线程写库，绝不拖垮压缩主流程；
  source=pre_compress 标记经 AddMemoryReq.metadata 落入 RawDocument.meta。
- lane="chat" 刻意选择：3 天半衰期，抢救内容不长期污染检索。
"""
import logging
import threading

from lantai.models.schemas import AddMemoryReq
from lantai.services.memory_service import add_memory

logger = logging.getLogger("lantai.pre_compress")

_MAX_CHARS = 600
_DEFAULT_N = 12
_MIN_CHARS = 10  # AddMemoryReq.content 的 pydantic min_length


def _render(messages: list[dict], n: int = _DEFAULT_N) -> str | None:
    """纯函数：抽 user/assistant 非空文本，逐条截断；全为 tool/空 → None。"""
    lines = []
    for m in (messages or [])[-n:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue  # tool 调用整轮跳过
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        lines.append(f"{role.capitalize()}: {content.strip()[:_MAX_CHARS]}")
    return "\n".join(lines) if lines else None


def flush_before_compress(messages: list[dict], n: int = _DEFAULT_N) -> dict:
    """把最近对话快照异步写入记忆库。返回是否触发写入及原因。"""
    text = _render(messages, n)
    if text is None:
        return {"flushed": False, "reason": "no_plain_text"}
    if len(text) < _MIN_CHARS:
        return {"flushed": False, "reason": "too_short"}

    def _worker() -> None:
        try:
            add_memory(AddMemoryReq(
                title="压缩前会话快照",
                content=text,
                lane="chat",
                metadata={"source": "pre_compress"},
            ))
        except Exception:
            # 抢救失败绝不影响压缩主流程；只记日志
            logger.exception("pre_compress flush failed")

    threading.Thread(target=_worker, daemon=True).start()
    return {"flushed": True, "chars": len(text)}
