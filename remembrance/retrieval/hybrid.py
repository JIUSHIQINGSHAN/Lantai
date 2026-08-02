from rank_bm25 import BM25Okapi
import numpy as np
from sqlmodel import select

from remembrance.llm.client import embed
from remembrance.models.tables import MemoryItem
from remembrance.storage import db
from remembrance.core.settings import settings
from remembrance.retrieval.intent import classify_intent
from remembrance.retrieval.reranker import rerank
from remembrance.storage.vector_store import get_vector_store


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

    # Step 2: 向量检索（ChromaDB HNSW 索引）
    fetch_n = candidate_n * settings.RERANKER_CANDIDATE_MULTIPLIER
    qv = embed([query])[0]
    vector_store = get_vector_store()
    vector_results = vector_store.search(qv, top_k=fetch_n)

    if not vector_results:
        return []

    # 从 SQLite 加载完整记忆项（仅候选集，不是全表）
    ids = [r["id"] for r in vector_results]
    with db.get_session() as s:
        items = s.exec(select(MemoryItem).where(MemoryItem.id.in_(ids))).all()
    items_by_id = {m.id: m for m in items}

    # 过滤 memory_types / lanes
    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    if lanes:
        items = [m for m in items if m.lane in lanes]

    # Step 2.5: Chronos 双时间过滤
    from remembrance.core.time import utcnow
    now = utcnow()
    temporally_valid = []
    for m in items:
        if m.valid_from and m.valid_from > now:
            continue  # 未生效，跳过
        if m.valid_to and m.valid_to < now:
            m.decay_score *= 0.3  # 过期降权
        temporally_valid.append(m)
    items = temporally_valid

    if not items:
        return []

    # Step 3: BM25 + 向量 + 衰减 混合打分
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

    # Step 4: Reranker（可选）
    if use_rerank and settings.RERANKER_ENABLED and candidates:
        docs = [m.content for _, m in candidates]
        reranked = rerank(query, docs, top_k)
        if reranked:
            return [{"score": r["score"], "document": r["document"]} for r in reranked]

    return [{"score": s, "memory": m.model_dump(mode="json")}
            for s, m in candidates[:top_k]]


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
