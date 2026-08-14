"""余弦相似度去重——在 candidate 创建时、gate 之前执行

两路径预判（ADR-0019）：
- fastpath（直书）路径：merge ≥ DEDUP_MERGE_THRESHOLD(0.90)，update ∈ [UPDATE, MERGE)，
  insert < UPDATE —— 直书句型高频、真重复多，纯余弦足够。
- LLM 提取路径：merge ≥ DEDUP_PRESCREEN_MERGE(0.95)（直合，不提取）；
  undecided ∈ [UPDATE, PRESCREEN)（提取后交结构判别 relation.py）；
  insert < UPDATE。
"""
from lantai.models.tables import MemoryItem
from lantai.core.settings import settings


def find_similar(session, query_results: list[dict], fastpath: bool = False) -> tuple[str, MemoryItem | None, float]:
    """对候选与现有 active 记忆做余弦预判。

    返回 (action, target_memory_or_None, best_sim)。
    action: "merge" | "update" | "undecided" | "insert"
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

    merge_t = (settings.DEDUP_MERGE_THRESHOLD if fastpath
               else settings.DEDUP_PRESCREEN_MERGE)
    update_t = settings.DEDUP_UPDATE_THRESHOLD
    if best_sim >= merge_t:
        return "merge", best_mem, best_sim
    if best_sim >= update_t:
        # fastpath 中带 = update 提案；提取路径中带 = undecided（结构判别）
        return ("update" if fastpath else "undecided"), best_mem, best_sim
    return "insert", None, best_sim
