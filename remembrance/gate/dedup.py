"""余弦相似度去重——在 candidate 创建时、gate 之前执行

三态判定：merge / update / insert
阈值：>merge → merge/update，>update → update
"""
from remembrance.models.tables import MemoryItem
from remembrance.core.settings import settings


def find_similar(session, query_results: list[dict]) -> tuple[str, MemoryItem | None, float]:
    """对候选与现有 active 记忆做三态判定。

    返回 (action, target_memory_or_None, best_sim)。
    action: "merge" | "update" | "insert"
    """
    best_sim = 0.0
    best_mem = None
    for r in query_results:
        mem = session.get(MemoryItem, r["id"])
        if not mem or mem.status != "active":
            continue
        sim = 1.0 - r["distance"]  # cosine 距离 → 相似度
        if sim > best_sim:
            best_sim = sim
            best_mem = mem

    if best_mem is None:
        return "insert", None, 0.0
    if best_sim >= settings.DEDUP_MERGE_THRESHOLD:
        return "merge", best_mem, best_sim
    if best_sim >= settings.DEDUP_UPDATE_THRESHOLD:
        return "update", best_mem, best_sim
    return "insert", None, best_sim
