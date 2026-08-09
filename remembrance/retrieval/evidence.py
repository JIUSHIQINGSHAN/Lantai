"""检索透明（Ticket 04）——从检索结果提取"来源说明"。

被 MCP search 与 REST /search 共用：每条 {id, content[:200], score}。
- 非 rerank 结果：{"score", "memory": {id, content}} 直接取；
- rerank 结果：{"score", "document"} 无 id → 按内容反查 DB 拿 id
  （查不到给 None，内容摘要仍保留，不阻断）；
- 空/异常 → 空列表，零侵入。
"""
from sqlmodel import select

from remembrance.models.tables import MemoryItem
from remembrance.storage import db


def build_evidence(results: list, limit: int = 10) -> list:
    """检索结果 → 来源说明列表。"""
    if not results:
        return []
    evidence = []
    docs_to_resolve = []
    try:
        for r in results:
            mem = r.get("memory")
            if mem and mem.get("id"):
                evidence.append({
                    "id": mem["id"],
                    "content": (mem.get("content") or "")[:200],
                    "score": r.get("score"),
                })
            elif r.get("document"):
                docs_to_resolve.append(r)
            if len(evidence) >= limit:
                return evidence
        if docs_to_resolve:
            with db.get_session() as s:
                for r in docs_to_resolve:
                    doc = r["document"]
                    m = s.exec(select(MemoryItem)
                               .where(MemoryItem.content == doc,
                                      MemoryItem.status == "active")).first()
                    evidence.append({
                        "id": m.id if m is not None else None,
                        "content": doc[:200],
                        "score": r.get("score"),
                    })
        return evidence[:limit]
    except Exception:
        return []
