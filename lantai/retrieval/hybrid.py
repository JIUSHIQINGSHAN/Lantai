import time
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from sqlmodel import select

from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.llm.client import embed
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.retrieval.intent import classify_intent
from lantai.retrieval.reranker import rerank
from lantai.storage import db
from lantai.storage.fts import search_fts, search_fts_bm25
from lantai.storage.vector_store import get_vector_store


@dataclass(frozen=True)
class RetrievalParams:
    """不可变检索参数快照（F3 修复：并发安全，杜绝修改全局 settings 单例）。"""
    w_vector: float = field(default_factory=lambda: float(settings.RETRIEVAL_W_VECTOR))
    w_bm25: float = field(default_factory=lambda: float(settings.RETRIEVAL_W_BM25))
    w_fts: float = field(default_factory=lambda: float(settings.RETRIEVAL_W_FTS))
    w_decay: float = field(default_factory=lambda: float(settings.RETRIEVAL_W_DECAY))
    lane_boost: dict = field(default_factory=lambda: dict(settings.LANE_RETRIEVAL_BOOST))
    reranker_multiplier: int = field(default_factory=lambda: int(settings.RERANKER_CANDIDATE_MULTIPLIER))
    reranker_enabled: bool = field(default_factory=lambda: bool(settings.RERANKER_ENABLED))
    verbatim_in_recall: bool = field(default_factory=lambda: bool(settings.VERBATIM_IN_RECALL))
    fts_recall_top_k: int = field(default_factory=lambda: int(settings.FTS_RECALL_TOP_K))
    supersedes_enabled: bool = field(default_factory=lambda: bool(settings.SUPERSEDES_ORDERING_ENABLED))
    supersedes_demote_epsilon: float = field(default_factory=lambda: float(settings.SUPERSEDES_DEMOTE_EPSILON))
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_overrides(cls, overrides: dict | None = None) -> "RetrievalParams":
        """从覆盖字典构造不可变快照，支持 settings 大写名及 dataclass 小写名。"""
        if not overrides:
            return cls()
        mapping = {
            "RETRIEVAL_W_VECTOR": "w_vector",
            "RETRIEVAL_W_BM25": "w_bm25",
            "RETRIEVAL_W_FTS": "w_fts",
            "RETRIEVAL_W_DECAY": "w_decay",
            "LANE_RETRIEVAL_BOOST": "lane_boost",
            "RERANKER_CANDIDATE_MULTIPLIER": "reranker_multiplier",
            "RERANKER_ENABLED": "reranker_enabled",
            "VERBATIM_IN_RECALL": "verbatim_in_recall",
            "FTS_RECALL_TOP_K": "fts_recall_top_k",
            "SUPERSEDES_ORDERING_ENABLED": "supersedes_enabled",
            "SUPERSEDES_DEMOTE_EPSILON": "supersedes_demote_epsilon",
        }
        known = {}
        extra = {}
        for k, v in overrides.items():
            attr = mapping.get(k, k)
            if attr in cls.__dataclass_fields__ and attr != "extra":
                known[attr] = v
            else:
                extra[k] = v
        return cls(**known, extra=extra)

    def get(self, key: str, default: Any = None) -> Any:
        mapping = {
            "RETRIEVAL_W_VECTOR": "w_vector",
            "RETRIEVAL_W_BM25": "w_bm25",
            "RETRIEVAL_W_FTS": "w_fts",
            "RETRIEVAL_W_DECAY": "w_decay",
            "LANE_RETRIEVAL_BOOST": "lane_boost",
            "RERANKER_CANDIDATE_MULTIPLIER": "reranker_multiplier",
            "RERANKER_ENABLED": "reranker_enabled",
            "VERBATIM_IN_RECALL": "verbatim_in_recall",
            "FTS_RECALL_TOP_K": "fts_recall_top_k",
            "SUPERSEDES_ORDERING_ENABLED": "supersedes_enabled",
            "SUPERSEDES_DEMOTE_EPSILON": "supersedes_demote_epsilon",
        }
        attr = mapping.get(key, key)
        if hasattr(self, attr):
            return getattr(self, attr)
        return self.extra.get(key, getattr(settings, key, default))





def _apply_supersedes_order(scored: list, breakdowns: dict | None = None,
                            params: RetrievalParams | None = None,
                            session: Any = None) -> list:
    """supersedes 边感知重排序。新值顶替旧值，将旧值压入新值之下。

    宁 miss 不脏写的实践：旧值在校正、证实前保留，
    新值若在候选集时，新值的确信度应比旧值好，旧值被取代。
    边不存在、异常/缺边均默认视为原样。
    """
    p = params or RetrievalParams()
    if not p.supersedes_enabled or not scored:
        return scored
    ids = [m.id for _, m in scored]
    superseded_by: dict[str, list[str]] = {}
    try:
        def _get_s(cb):
            if session is not None:
                return cb(session)
            with db.get_session() as s:
                return cb(s)
        def _edge_cb(s):
            return s.exec(select(MemoryEdge).where(
                MemoryEdge.relation == "supersedes",
                MemoryEdge.source_memory_id.in_(ids),
                MemoryEdge.target_memory_id.in_(ids),
            )).all()
        edges = _get_s(_edge_cb)
    except Exception:
        return scored
    for e in edges:
        superseded_by.setdefault(e.target_memory_id, []).append(e.source_memory_id)
    if not superseded_by:
        return scored
    id_to_score = {m.id: sc for sc, m in scored}
    out = []
    for sc, m in scored:
        superseder_ids = [n for n in superseded_by.get(m.id, []) if n in id_to_score]
        if superseder_ids:
            sc = min(sc, max(id_to_score[n] for n in superseder_ids)
                      - p.supersedes_demote_epsilon)
            # 检索透明：explain 里标注被哪条新值降权（可审计，ADR-0008 溯源精神）
            if breakdowns is not None and m.id in breakdowns:
                breakdowns[m.id]["superseded_by"] = superseder_ids
                breakdowns[m.id]["demoted"] = True
        out.append((sc, m))
    out.sort(key=lambda x: -x[0])
    return out

def hybrid_search(
    query: str,
    top_k: int = 5,
    memory_types: list[str] | None = None,
    lanes: list[str] | None = None,
    use_rerank: bool = True,
    trace: bool = False,
    explain: bool = False,
    param_overrides: dict | None = None,
    domain: str | None = None,
    session: Any = None,
    params: RetrievalParams | None = None,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """混合检索：向量 + BM25 + 衰减（支持辨域 ADR-0034 domain 过滤）。

    trace=True 时返回 (results, trace_steps)。
    explain=True 时每条结果附带分项 {vector, bm25, fts, decay, lane_boost,
    final, decay_class, decay_multiplier}（reranker 开启时也保留原始分项）。

    param_overrides: 临时覆盖 settings 检索参数（如 {"RETRIEVAL_W_VECTOR": 0.7}）。
    通过局部快照 RetrievalParams 注入，不再修改全局 settings 单例（并发安全，F3）。
    """
    effective_params = params or RetrievalParams.from_overrides(param_overrides)
    return _hybrid_search_impl(
        query, top_k, memory_types, lanes, use_rerank, trace, explain,
        domain=domain, session=session, params=effective_params,
    )



def _param_override(overrides: dict | None):
    """上下文管理器：临时覆盖 settings 属性，退出恢复。"""
    import contextlib
    @contextlib.contextmanager
    def _ctx():
        if not overrides:
            yield
            return
        saved = {}
        for key, val in overrides.items():
            if hasattr(settings, key):
                saved[key] = getattr(settings, key)
                with contextlib.suppress(Exception):
                    setattr(settings, key, val)
        try:
            yield
        finally:
            for key, val in saved.items():
                with contextlib.suppress(Exception):
                    setattr(settings, key, val)
    return _ctx()


def _hybrid_search_impl(
    query: str,
    top_k: int = 5,
    memory_types: list[str] | None = None,
    lanes: list[str] | None = None,
    use_rerank: bool = True,
    trace: bool = False,
    explain: bool = False,
    domain: str | None = None,
    session: Any = None,
    params: RetrievalParams | None = None,
) -> list[dict] | tuple[list[dict], list[dict]]:
    p = params or RetrievalParams()
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

    # Step 2: 向量检索（ChromaDB HNSW 索引，异常平滑降级：拾遗 ADR-0028）
    fetch_n = candidate_n * p.reranker_multiplier
    vector_results = []
    try:
        qv = embed([query])[0]
        vector_store = get_vector_store()
        vector_results = vector_store.search(qv, top_k=fetch_n)
        # ADR-0008: Drop irrelevant vector results (Chroma pads up to top_k)
        vector_results = [r for r in vector_results if r.get("distance", 1.0) < 0.8]
    except Exception as e:
        logger.warning(f"Vector search failed, falling back to keyword (Shiyi): {e}")
        vector_results = []

    if trace:
        t2 = time.perf_counter()
        scores = [1.0 - r["distance"] for r in vector_results] if vector_results else []
        trace_steps.append({
            "step": "vector_search", "elapsed_ms": round((t2 - t1) * 1000, 1),
            "candidate_count": len(vector_results),
            "score_range": [round(min(scores), 3), round(max(scores), 3)] if scores else None,
            "fallback": not vector_results,
        })

    if not vector_results:
        # 向量检索失败（embedding 超时/401/未配置/空库）→ FTS5 + BM25 兜底（拾遗），降级可用而非零召回
        return _keyword_fallback(
            query, top_k, fetch_n, memory_types, lanes,
            trace, trace_steps, t0, explain,
            domain=domain, session=session, params=p,
        )

    # Step 3: FTS5 子串召回（ADR-0008）与 BM25 召回（F2 重构）
    fts_hits: set[str] = set()
    fts_bm25_results = []
    fts_hits: set[str] = set()
    try:
        def _get_s(cb):
            if session is not None:
                return cb(session)
            with db.get_session() as s:
                return cb(s)
                
        def _search_fts_cb(s):
            return set(search_fts(
                s.connection().connection.driver_connection,
                query, top_k=p.fts_recall_top_k))
        fts_hits = _get_s(_search_fts_cb)
        
        def _search_fts_bm25_cb(s):
            return search_fts_bm25(
                s.connection().connection.driver_connection,
                query, top_k=fetch_n)
        fts_bm25_results = _get_s(_search_fts_bm25_cb)
    except Exception:
        pass

    # 从 SQLite 加载完整记忆项（Vector, BM25 和 FTS 的并集）
    vector_ids = [r["id"] for r in vector_results]
    bm25_ids = [r[0] for r in fts_bm25_results]
    all_ids = set(vector_ids) | set(bm25_ids) | fts_hits
    if not all_ids:
        if trace:
            return [], trace_steps
        return []

    def _query_items(s):
        return s.exec(
            select(MemoryItem)
            .where(MemoryItem.id.in_(list(all_ids)))
            .where(MemoryItem.status == "active")
        ).all()

    if session is not None:
        items = _query_items(session)
    else:
        with db.get_session() as s:
            items = _query_items(s)

    # 过滤 memory_types / lanes / domain
    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    elif not p.verbatim_in_recall:
        items = [m for m in items if m.memory_type != "verbatim"]
    if lanes:
        items = [m for m in items if m.lane in lanes]
    if domain and domain != "all":
        items = [m for m in items if getattr(m, "domain", "user") == domain]

    items = _chronos_filter(items)

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

    # Step 4: RRF (Reciprocal Rank Fusion) + 衰减 融合打分 (F2)
    vector_ranks = {r["id"]: idx for idx, r in enumerate(vector_results)}
    bm25_ranks = {r[0]: idx for idx, r in enumerate(fts_bm25_results)}
    
    scored_items = []
    breakdowns: dict[str, dict] = {}
    rrf_k = 60
    
    for m in items:
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = p.lane_boost.get(lane, 1.0)
        persona_boost = 1.05 if lane in ("preference", "rule") else 1.0
        fts_hit = 1.0 if m.id in fts_hits else 0.0
        
        # RRF 分数计算，并根据权重放大以与原来量级对齐
        rrf_vec = 1.0 / (rrf_k + vector_ranks[m.id] + 1) if m.id in vector_ranks else 0.0
        rrf_bm = 1.0 / (rrf_k + bm25_ranks[m.id] + 1) if m.id in bm25_ranks else 0.0
        
        # 将 RRF 分数放大（因为 1/61 约等于 0.016，乘以常数让它回到接近 1.0 的量级，或者直接接受新分数）
        RRF_SCALE = 60.0 
        vec_score = p.w_vector * rrf_vec * RRF_SCALE
        bm25_score = p.w_bm25 * rrf_bm * RRF_SCALE
        
        score = (vec_score
                 + bm25_score
                 + p.w_fts * fts_hit
                 + p.w_decay * m.decay_score) * lane_boost * persona_boost
                 
        scored_items.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": round(vec_score, 4),
                "bm25": round(bm25_score, 4),
                "fts": round(p.w_fts * fts_hit, 4),
                "decay": round(p.w_decay * m.decay_score, 4),
                "lane_boost": lane_boost,
                "persona_boost": persona_boost,
                "final": round(score, 4),
                "decay_class": m.decay_class,
                "decay_multiplier": round(_age_multiplier(m), 4),
            }

    # 按照分数降序排列
    scored_items.sort(key=lambda x: x[0], reverse=True)

    scored_items = _apply_supersedes_order(scored_items, breakdowns, params=p, session=session)
    candidates = scored_items[:fetch_n]

    # Step 5: Reranker（可选）
    if use_rerank and p.reranker_enabled and candidates:
        docs = [m.content for _, m in candidates]
        reranked = rerank(query, docs, top_k)
        if reranked:
            doc_to_m = {m.content: m for _, m in candidates}
            reranked_scored = []
            for r in reranked:
                idx = r.get("index")
                if idx is not None and 0 <= idx < len(candidates):
                    reranked_scored.append((r["score"], candidates[idx][1]))
                elif r.get("document") in doc_to_m:
                    reranked_scored.append((r["score"], doc_to_m[r["document"]]))
            reranked_scored = _apply_supersedes_order(reranked_scored, breakdowns, params=p)
            results = []
            for s, m in reranked_scored:
                item = {
                    "score": s,
                    "memory": m.model_dump(mode="json"),
                    "document": m.content,
                }
                if explain:
                    item["explain"] = breakdowns.get(m.id)
                results.append(item)
            if trace:
                t4 = time.perf_counter()
                rr_scores = [r["score"] for r in reranked]
                trace_steps.append({
                    "step": "reranker",
                    "time_ms": round((t4 - t3) * 1000, 2),
                    "model": "bge-reranker-v2-m3",
                    "scores": rr_scores,
                })
            if trace:
                return results, trace_steps
            return results

    results = []
    for s, m in candidates[:top_k]:
        item = {
            "score": s,
            "memory": m.model_dump(mode="json"),
            "document": m.content,
        }
        if explain:
            item["explain"] = breakdowns.get(m.id)
        results.append(item)
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


def _chronos_filter(items: list) -> list:
    """Chronos 双时间过滤：未到 valid_from 的剔除，已过 valid_to 的衰减到 0.3 倍。"""
    from lantai.core.time import utcnow
    now = utcnow()
    temporally_valid = []
    for m in items:
        vf = m.valid_from
        if vf and vf.tzinfo is None:
            vf = vf.replace(tzinfo=UTC)
        vt = m.valid_to
        if vt and vt.tzinfo is None:
            vt = vt.replace(tzinfo=UTC)
        if vf and vf > now:
            continue
        if vt and vt < now:
            m.decay_score *= 0.3
        temporally_valid.append(m)
    return temporally_valid


def _age_multiplier(m) -> float:
    """按衰减类计算 decay_multiplier（调试字段；procedural 恒 1.0）。"""
    from lantai.core.time import utcnow as _utcnow
    from lantai.memory.decay_class import decay_multiplier as _dm
    last = m.last_used_at or m.created_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    days = max(0.0, (_utcnow() - last).total_seconds() / 86400.0)
    return _dm(getattr(m, "decay_class", "episodic"), days)


def _keyword_fallback(
    query: str,
    top_k: int,
    fetch_n: int,
    memory_types: list[str] | None,
    lanes: list[str] | None,
    trace: bool,
    trace_steps: list,
    t0: float,
    explain: bool = False,
    domain: str | None = None,
    session: Any = None,
    params: RetrievalParams | None = None,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """向量检索降级路径：FTS5 召回作候选集，BM25 + decay 打分（无向量分）。

    权重归一化到 1：只使用 (W_BM25 + W_FTS + W_DECAY)。
    不触发 reranker——embedding 已不可用，rerank 大概率同样失败，保持降级可用。
    """
    p = params or RetrievalParams()
    fts_bm25_results = []
    fts_hits: set[str] = set()

    def _get_s(cb):
        if session is not None:
            return cb(session)
        with db.get_session() as s:
            return cb(s)

    try:
        def _fts_bm25_cb(s):
            return search_fts_bm25(
                s.connection().connection.driver_connection,
                query, top_k=max(fetch_n, p.fts_recall_top_k))
        fts_bm25_results = _get_s(_fts_bm25_cb)

        def _fts_hit_cb(s):
            return search_fts(
                s.connection().connection.driver_connection,
                query, top_k=max(fetch_n, p.fts_recall_top_k))
        fts_hits = set(_get_s(_fts_hit_cb))
    except Exception:
        pass

    candidate_ids = set(r[0] for r in fts_bm25_results) | fts_hits

    # ADR-0028
    clean_q = query.strip()
    import re
    clean_q_no_punct = re.sub(r'[^\w\u4e00-\u9fa5]+', ' ', clean_q).strip()
    tokens = [w for w in clean_q_no_punct.split() if w.strip()]
    if not tokens:
        tokens = [w for w in list(clean_q.replace(" ", "")) if w.strip()]
    
    has_short_tokens = any(len(t) < 3 for t in tokens)

    if (not candidate_ids or has_short_tokens) and tokens:
        try:
            from sqlalchemy import or_
            def _like_cb(s):
                conditions = [MemoryItem.content.like(f"%{t}%") for t in tokens]
                return s.exec(
                    select(MemoryItem.id)
                    .where(MemoryItem.status == "active")
                    .where(or_(*conditions))
                    .limit(max(fetch_n, p.fts_recall_top_k))
                ).all()
            like_items = _get_s(_like_cb)
            candidate_ids |= set(like_items)
        except Exception:
            pass


    if not candidate_ids:
        if trace:
            return [], trace_steps
        return []

    def _items_cb(s):
        return s.exec(select(MemoryItem)
                       .where(MemoryItem.id.in_(list(candidate_ids)),
                              MemoryItem.status == "active")).all()

    items = _get_s(_items_cb)

    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    elif not p.verbatim_in_recall:
        items = [m for m in items if m.memory_type != "verbatim"]
    if lanes:
        items = [m for m in items if m.lane in lanes]
    if domain and domain != "all":
        items = [m for m in items if getattr(m, "domain", "user") == domain]
    items = _chronos_filter(items)


    if not items:
        if trace:
            return [], trace_steps
        return []

    # FTS BM25 + decay 融合
    bm25_ranks = {r[0]: idx for idx, r in enumerate(fts_bm25_results)}
    total_w = (p.w_bm25 + p.w_fts + p.w_decay)
    rrf_k = 60
    
    scored = []
    breakdowns: dict[str, dict] = {}
    for m in items:
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = p.lane_boost.get(lane, 1.0)
        
        rrf_bm = 1.0 / (rrf_k + bm25_ranks[m.id] + 1) if m.id in bm25_ranks else 0.0
        bm25_score = p.w_bm25 * rrf_bm * 60.0
        fts_hit = 1.0 if m.id in fts_hits else 0.0
        
        score = (
            (bm25_score
             + p.w_fts * fts_hit
             + p.w_decay * m.decay_score)
            / total_w
        ) * lane_boost
        scored.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": 0.0,
                "bm25": round(bm25_score / total_w, 4),
                "fts": round(p.w_fts * fts_hit / total_w, 4),
                "decay": round(p.w_decay * m.decay_score / total_w, 4),
                "lane_boost": lane_boost,
                "final": round(score, 4),
                "decay_class": m.decay_class,
                "decay_multiplier": round(_age_multiplier(m), 4),
            }

    scored = _apply_supersedes_order(scored, breakdowns, params=p)
    results = []
    for s, m in scored[:top_k]:
        item = {
            "score": s,
            "memory": m.model_dump(mode="json"),
            "document": m.content,
        }
        if explain:
            item["explain"] = breakdowns.get(m.id)
        results.append(item)

    if trace:
        t4 = time.perf_counter()
        final_scores = [s for s, _ in scored[:top_k]]
        trace_steps.append({
            "step": "fallback_fts", "elapsed_ms": round((t4 - t0) * 1000, 1),
            "candidate_count": len(results),
            "score_range": [round(min(final_scores), 3), round(max(final_scores), 3)] if final_scores else None,
        })
        return results, trace_steps
    return results
