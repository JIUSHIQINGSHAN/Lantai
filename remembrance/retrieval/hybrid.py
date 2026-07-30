from rank_bm25 import BM25Okapi
import numpy as np
from sqlmodel import select

from remembrance.llm.client import embed
from remembrance.models.tables import MemoryItem
from remembrance.storage import db
from remembrance.core.settings import settings
from remembrance.retrieval.intent import classify_intent
from remembrance.retrieval.reranker import rerank


def _cos(a, b):
    a, b = np.array(a), np.array(b)
    if not a.any() or not b.any():
        return 0.0
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def hybrid_search(query: str, top_k: int = 5,
                  memory_types: list[str] | None = None,
                  lanes: list[str] | None = None,
                  use_rerank: bool = True) -> list[dict]:
    # Step 1: 意图分类，确定候选集大小
    intent_info = classify_intent(query)
    candidate_n = intent_info["candidate_n"]

    # Step 2: 混合检索，返回 candidate_n * multiplier 条候选
    fetch_n = candidate_n * settings.RERANKER_CANDIDATE_MULTIPLIER
    with db.get_session() as s:
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        if memory_types:
            stmt = stmt.where(MemoryItem.memory_type.in_(memory_types))
        if lanes:
            stmt = stmt.where(MemoryItem.lane.in_(lanes))
        items = s.exec(stmt).all()

    if not items:
        return []

    qv = embed([query])[0]
    corpus = [m.content.split() for m in items]
    bm25 = BM25Okapi(corpus)
    bm_scores = bm25.get_scores(query.split())
    bm_norm = (bm_scores - bm_scores.min()) / (bm_scores.ptp() + 1e-8)

    scored_items = []
    for i, m in enumerate(items):
        vs = _cos(qv, m.embedding) if m.embedding else 0.0
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = settings.LANE_RETRIEVAL_BOOST.get(lane, 1.0)
        score = (0.6 * vs + 0.3 * float(bm_norm[i]) + 0.1 * m.decay_score) * lane_boost
        scored_items.append((score, m))

    scored_items.sort(key=lambda x: -x[0])
    candidates = scored_items[:fetch_n]

    # Step 3: Reranker（可选）
    if use_rerank and settings.RERANKER_ENABLED and candidates:
        docs = [m.content for _, m in candidates]
        reranked = rerank(query, docs, top_k)
        if reranked:
            return [{"score": r["score"], "document": r["document"]} for r in reranked]
        # 降级：reranker 失败，返回混合检索结果

    return [{"score": s, "memory": m.model_dump(mode="json")}
            for s, m in candidates[:top_k]]
