from rank_bm25 import BM25Okapi
import numpy as np
from sqlmodel import select

from remembrance.llm.client import embed
from remembrance.models.tables import MemoryItem
from remembrance.storage.db import get_session


def _cos(a, b):
    a, b = np.array(a), np.array(b)
    if not a.any() or not b.any():
        return 0.0
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def hybrid_search(query: str, top_k: int = 5,
                  memory_types: list[str] | None = None) -> list[dict]:
    with get_session() as s:
        stmt = select(MemoryItem).where(MemoryItem.status == "active")
        if memory_types:
            stmt = stmt.where(MemoryItem.memory_type.in_(memory_types))
        items = s.exec(stmt).all()

    if not items:
        return []

    qv = embed([query])[0]
    corpus = [m.content.split() for m in items]
    bm25 = BM25Okapi(corpus)
    bm_scores = bm25.get_scores(query.split())
    bm_norm = (bm_scores - bm_scores.min()) / (bm_scores.ptp() + 1e-8)

    results = []
    for i, m in enumerate(items):
        vs = _cos(qv, m.embedding) if m.embedding else 0.0
        score = 0.6 * vs + 0.3 * float(bm_norm[i]) + 0.1 * m.decay_score
        results.append((score, m))
    results.sort(key=lambda x: -x[0])
    return [{"score": s, "memory": m.model_dump(mode="json")}
            for s, m in results[:top_k]]
