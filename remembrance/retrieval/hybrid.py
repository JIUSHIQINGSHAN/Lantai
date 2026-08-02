import jieba
import time
from rank_bm25 import BM25Okapi
from sqlmodel import select

from remembrance.llm.client import embed
from remembrance.models.tables import MemoryItem
from remembrance.storage import db
from remembrance.core.settings import settings
from remembrance.retrieval.intent import classify_intent
from remembrance.retrieval.reranker import rerank
from remembrance.storage.vector_store import get_vector_store
from remembrance.storage.fts import search_fts


# BM25 语料缓存（M4）：key = items 的 (id, content) 有序元组，命中即复用
_BM25_CACHE: dict = {"key": None, "bm25": None}


def _get_bm25(items: list) -> "BM25Okapi":
    key = tuple((m.id, m.content) for m in items)
    if _BM25_CACHE.get("key") == key:
        return _BM25_CACHE["bm25"]
    corpus = [jieba.lcut(m.content) for m in items]
    bm25 = BM25Okapi(corpus)
    _BM25_CACHE["key"] = key
    _BM25_CACHE["bm25"] = bm25
    return bm25


def hybrid_search(query: str, top_k: int = 5,
                  memory_types: list[str] | None = None,
                  lanes: list[str] | None = None,
                  use_rerank: bool = True,
                  trace: bool = False) -> list[dict] | tuple[list[dict], list[dict]]:
    """混合检索：向量 + BM25 + 衰减。

    trace=True 时返回 (results, trace_steps)。
    """
    trace_steps = []
    t0 = time.perf_counter()

    # Step 1: 意图分类
    intent_info = classify_intent(query)
    candidate_n = intent_info["candidate_n"]
    if trace:
        t1 = time.perf_counter()
        trace_steps.append({
            "step": "intent", "elapsed_ms": round((t1 - t0) * 1000, 1),
            "candidate_count": None, "score_range": None,
        })

    # Step 2: 向量检索（ChromaDB HNSW 索引）
    fetch_n = candidate_n * settings.RERANKER_CANDIDATE_MULTIPLIER
    qv = embed([query])[0]
    vector_store = get_vector_store()
    vector_results = vector_store.search(qv, top_k=fetch_n)

    if trace:
        t2 = time.perf_counter()
        scores = [1.0 - r["distance"] for r in vector_results] if vector_results else []
        trace_steps.append({
            "step": "vector_search", "elapsed_ms": round((t2 - t1) * 1000, 1),
            "candidate_count": len(vector_results),
            "score_range": [round(min(scores), 3), round(max(scores), 3)] if scores else None,
        })

    if not vector_results:
        if trace:
            return [], trace_steps
        return []

    # 从 SQLite 加载完整记忆项——仅 active
    ids = [r["id"] for r in vector_results]
    with db.get_session() as s:
        items = s.exec(
            select(MemoryItem)
            .where(MemoryItem.id.in_(ids))
            .where(MemoryItem.status == "active")
        ).all()
    items_by_id = {m.id: m for m in items}

    # 过滤 memory_types / lanes
    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    if lanes:
        items = [m for m in items if m.lane in lanes]

    # Step 2.5: Chronos 双时间过滤（DB 读出的 datetime 为 naive，需先归一时区）
    from datetime import timezone
    from remembrance.core.time import utcnow
    now = utcnow()
    temporally_valid = []
    for m in items:
        vf = m.valid_from
        if vf and vf.tzinfo is None:
            vf = vf.replace(tzinfo=timezone.utc)
        vt = m.valid_to
        if vt and vt.tzinfo is None:
            vt = vt.replace(tzinfo=timezone.utc)
        if vf and vf > now:
            continue
        if vt and vt < now:
            m.decay_score *= 0.3
        temporally_valid.append(m)
    items = temporally_valid

    if trace:
        t3 = time.perf_counter()
        trace_steps.append({
            "step": "decay_filter", "elapsed_ms": round((t3 - t2) * 1000, 1),
            "candidate_count": len(items), "score_range": None,
        })

    if not items:
        if trace:
            return [], trace_steps
        return []

    # Step 3: FTS5 子串召回（ADR-0008）
    fts_hits: set[str] = set()
    try:
        with db.get_session() as s:
            fts_hits = set(search_fts(
                s.connection().connection.driver_connection,
                query, top_k=settings.FTS_RECALL_TOP_K))
    except Exception:
        fts_hits = set()  # FTS 不可用不影响主检索

    # Step 4: BM25（带缓存，M4）+ 向量距离 + FTS 命中 + 衰减 融合打分
    distances = {r["id"]: r["distance"] for r in vector_results}
    bm25 = _get_bm25(items)
    bm_scores = bm25.get_scores(jieba.lcut(query))
    # 不用 ndarray.ptp()（numpy>=2 已移除），min/max 兼容各版本
    bm_range = bm_scores.max() - bm_scores.min()
    bm_norm = (bm_scores - bm_scores.min()) / (bm_range + 1e-8)

    scored_items = []
    for i, m in enumerate(items):
        vs = 1.0 - distances.get(m.id, 1.0)
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = settings.LANE_RETRIEVAL_BOOST.get(lane, 1.0)
        fts_hit = 1.0 if m.id in fts_hits else 0.0
        score = (settings.RETRIEVAL_W_VECTOR * vs
                 + settings.RETRIEVAL_W_BM25 * float(bm_norm[i])
                 + settings.RETRIEVAL_W_FTS * fts_hit
                 + settings.RETRIEVAL_W_DECAY * m.decay_score) * lane_boost
        scored_items.append((score, m))

    # 追加召回：FTS 命中但未进向量候选的 active 记忆（并列召回，ADR-0008 决策 1）
    ranked_ids = {m.id for _, m in scored_items}
    extra_ids = fts_hits - ranked_ids
    if extra_ids:
        with db.get_session() as s:
            extra = s.exec(select(MemoryItem)
                           .where(MemoryItem.id.in_(extra_ids),
                                  MemoryItem.status == "active")).all()
        for m in extra:
            scored_items.append(
                (settings.RETRIEVAL_W_FTS + settings.RETRIEVAL_W_DECAY * m.decay_score, m))

    scored_items.sort(key=lambda x: -x[0])
    candidates = scored_items[:fetch_n]

    # Step 5: Reranker（可选）
    if use_rerank and settings.RERANKER_ENABLED and candidates:
        docs = [m.content for _, m in candidates]
        reranked = rerank(query, docs, top_k)
        if reranked:
            results = [{"score": r["score"], "document": r["document"]} for r in reranked]
            if trace:
                t4 = time.perf_counter()
                rr_scores = [r["score"] for r in reranked]
                trace_steps.append({
                    "step": "rerank", "elapsed_ms": round((t4 - t3) * 1000, 1),
                    "candidate_count": len(results),
                    "score_range": [round(min(rr_scores), 3), round(max(rr_scores), 3)],
                })
                trace_steps.append({
                    "step": "final", "elapsed_ms": round((t4 - t0) * 1000, 1),
                    "candidate_count": len(results),
                    "score_range": [round(min(rr_scores), 3), round(max(rr_scores), 3)],
                })
                return results, trace_steps
            return results

    results = [{"score": s, "memory": m.model_dump(mode="json")}
               for s, m in candidates[:top_k]]
    if trace:
        t4 = time.perf_counter()
        final_scores = [s for s, _ in candidates[:top_k]]
        trace_steps.append({
            "step": "final", "elapsed_ms": round((t4 - t0) * 1000, 1),
            "candidate_count": len(results),
            "score_range": [round(min(final_scores), 3), round(max(final_scores), 3)] if final_scores else None,
        })
        return results, trace_steps
    return results


def index_memory_item(memory_id: str, embedding: list[float], metadata: dict):
    """将记忆项索引到向量存储（创建/更新时调用）"""
    get_vector_store().add(
        ids=[memory_id],
        embeddings=[embedding],
        metadatas=[metadata],
    )


def delete_memory_item(memory_id: str):
    """从向量存储删除记忆项"""
    get_vector_store().delete([memory_id])
