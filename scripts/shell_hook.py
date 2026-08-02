"""Shell Hook——零依赖 CLI 注入路径

stdin 收 {user_message, ...} JSON
stdout 返回 {context: "..."} 或 {} (无结果)

2s 超时返回空，≤3 字符不注入，top_k=5 不开 rerank
返回 Markdown 列表格式带分数标注
"""
import sys
import json

# 最小依赖——避免 fastapi/testclient 启动开销
from remembrance.core.settings import settings
from remembrance.llm.client import embed
from remembrance.storage.vector_store import get_vector_store
from remembrance.storage import db
from remembrance.models.tables import MemoryItem
from sqlmodel import select


def search_context(query: str) -> str:
    """搜索记忆并返回 Markdown 格式上下文。"""
    if len(query) <= settings.SHELL_HOOK_MIN_CHARS:
        return ""

    try:
        qv = embed([query])[0]
        store = get_vector_store()
        results = store.search(qv, top_k=settings.SHELL_HOOK_TOP_K)
        if not results:
            return ""

        ids = [r["id"] for r in results]
        with db.get_session() as s:
            items = s.exec(
                select(MemoryItem)
                .where(MemoryItem.id.in_(ids))
                .where(MemoryItem.status == "active")
            ).all()

        lines = []
        for r in results:
            for m in items:
                if m.id == r["id"]:
                    score = round(1.0 - r["distance"], 2)
                    lines.append(f"- [{score}] {m.content[:200]}")
                    break
        return "\n".join(lines)
    except Exception:
        return ""


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw else {}
    except Exception:
        print(json.dumps({}))
        return

    query = data.get("user_message", "").strip()
    if not query or len(query) <= settings.SHELL_HOOK_MIN_CHARS:
        print(json.dumps({}))
        return

    context = search_context(query)
    print(json.dumps({"context": context} if context else {}))


if __name__ == "__main__":
    main()
