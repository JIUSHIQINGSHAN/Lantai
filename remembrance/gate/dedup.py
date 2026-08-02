"""余弦相似度去重——在 candidate 创建时、gate 之前执行

用余弦相似度（不用 Jaccard，对中文分词不敏感）
阈值：>merge → merge/update，>update → update
"""
from sqlmodel import select

from remembrance.llm.client import embed
from remembrance.models.tables import MemoryItem
from remembrance.storage import db
from remembrance.core.settings import settings


def find_similar(content: str, lane: str | None = None, top_k: int = 5) -> dict | None:
    """查找与 content 余弦相似度最高的已有记忆。

    返回 None 表示无相似记忆。
    返回 dict 包含 memory_id、similarity、action（merge/update）。
    """
    qv = embed([content])[0] if content else []
    if not qv:
        return None

    from remembrance.storage.vector_store import get_vector_store
    store = get_vector_store()
    results = store.search(qv, top_k=top_k)

    if not results:
        return None

    # 取最相似的
    best = results[0]
    similarity = 1.0 - best["distance"]  # cosine space: sim = 1 - dist

    merge_threshold = settings.DEDUP_MERGE_THRESHOLD
    update_threshold = settings.DEDUP_UPDATE_THRESHOLD

    if similarity >= merge_threshold:
        action = "merge"
    elif similarity >= update_threshold:
        action = "update"
    else:
        return None

    with db.get_session() as s:
        mem = s.get(MemoryItem, best["id"])
        if not mem or mem.status != "active":
            return None

        return {
            "memory_id": mem.id,
            "similarity": similarity,
            "action": action,
            "existing_content": mem.content,
        }
