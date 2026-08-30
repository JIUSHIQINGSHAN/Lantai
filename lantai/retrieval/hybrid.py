import jieba
import time
from rank_bm25 import BM25Okapi
from sqlmodel import select

from lantai.llm.client import embed
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage import db
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.retrieval.intent import classify_intent
from lantai.retrieval.reranker import rerank
from lantai.storage.vector_store import get_vector_store
from lantai.storage.fts import search_fts


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


def _apply_supersedes_order(scored: list, breakdowns: dict | None = None) -> list:
    """supersedes 边感知降权：被取代旧值若其新值同在候选集，压到新值之下。

    宁 miss 不脏写：不删除旧值（残留如实留在结果中），仅保证新值在前；
    新值不在候选集时不动旧值（有旧值可用总比空手好）。仅按候选 id 做一次
    边查找，异常/缺边静默降级为原排序。
    """
    if not settings.SUPERSEDES_ORDERING_ENABLED or not scored:
        return scored
    ids = [m.id for _, m in scored]
    superseded_by: dict[str, list[str]] = {}
    try:
        with db.get_session() as s:
            edges = s.exec(select(MemoryEdge).where(
                MemoryEdge.relation == "supersedes",
                MemoryEdge.source_memory_id.in_(ids),
                MemoryEdge.target_memory_id.in_(ids),
            )).all()
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
                      - settings.SUPERSEDES_DEMOTE_EPSILON)
            # 检索透明：explain 里标注被哪条新值降权（可审计，ADR-0008 溯源精神）
            if breakdowns is not None and m.id in breakdowns:
                breakdowns[m.id]["superseded_by"] = superseder_ids
                breakdowns[m.id]["demoted"] = True
        out.append((sc, m))
    out.sort(key=lambda x: -x[0])
    return out

def hybrid_search(query: str, top_k: int = 5,
                  memory_types: list[str] | None = None,
                  lanes: list[str] | None = None,
                  use_rerank: bool = True,
                  trace: bool = False,
                  explain: bool = False,
                  param_overrides: dict | None = None) -> list[dict] | tuple[list[dict], list[dict]]:
    """混合检索：向量 + BM25 + 衰减。

    trace=True 时返回 (results, trace_steps)。
    explain=True 时每条结果附带分项 {vector, bm25, fts, decay, lane_boost,
    final, decay_class, decay_multiplier}（reranker 开启时也保留原始分项）。

    param_overrides: 临时覆盖 settings 检索参数（如 {"RETRIEVAL_W_VECTOR": 0.7}）。
    仅本调用生效，结束后恢复；None 时行为与旧版本完全一致。
    """
    with _param_override(param_overrides):
        return _hybrid_search_impl(
            query, top_k, memory_types, lanes, use_rerank, trace, explain)


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
                try:
                    setattr(settings, key, val)
                except Exception:
                    pass
        try:
            yield
        finally:
            for key, val in saved.items():
                try:
                    setattr(settings, key, val)
                except Exception:
                    pass
    return _ctx()


def _hybrid_search_impl(query: str, top_k: int = 5,
                        memory_types: list[str] | None = None,
                        lanes: list[str] | None = None,
                        use_rerank: bool = True,
                        trace: bool = False,
                        explain: bool = False) -> list[dict] | tuple[list[dict], list[dict]]:
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
    fetch_n = candidate_n * settings.RERANKER_CANDIDATE_MULTIPLIER
    vector_results = []
    try:
        qv = embed([query])[0]
        vector_store = get_vector_store()
        vector_results = vector_store.search(qv, top_k=fetch_n)
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
        )

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
    elif not settings.VERBATIM_IN_RECALL:
        # 原文直存默认不进混合召回（Ticket 02）：GET /verbatim/search 专用通道可查
        items = [m for m in items if m.memory_type != "verbatim"]
    if lanes:
        items = [m for m in items if m.lane in lanes]

    # Step 2.5: Chronos 双时间过滤（DB 读出的 datetime 为 naive，需先归一时区）
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
    breakdowns: dict[str, dict] = {}
    for i, m in enumerate(items):
        vs = 1.0 - distances.get(m.id, 1.0)
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = settings.LANE_RETRIEVAL_BOOST.get(lane, 1.0)
        persona_boost = 1.05 if lane in ("preference", "rule") else 1.0
        fts_hit = 1.0 if m.id in fts_hits else 0.0
        bm_val = float(bm_norm[i])
        score = (settings.RETRIEVAL_W_VECTOR * vs
                 + settings.RETRIEVAL_W_BM25 * bm_val
                 + settings.RETRIEVAL_W_FTS * fts_hit
                 + settings.RETRIEVAL_W_DECAY * m.decay_score) * lane_boost * persona_boost
        scored_items.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": round(settings.RETRIEVAL_W_VECTOR * vs, 4),
                "bm25": round(settings.RETRIEVAL_W_BM25 * bm_val, 4),
                "fts": round(settings.RETRIEVAL_W_FTS * fts_hit, 4),
                "decay": round(settings.RETRIEVAL_W_DECAY * m.decay_score, 4),
                "lane_boost": lane_boost,
                "persona_boost": persona_boost,
                "final": round(score, 4),
                "decay_class": m.decay_class,
                # decay_multiplier 是 decay_class 理论半衰期参考（0.5^(age/hl)）；
                # 实际打分中的 decay 分项用的是 forgetting 的 lane-strength 指数衰减
                # （m.decay_score），两者不同源，decay_multiplier 仅供观测。
                "decay_multiplier": round(_age_multiplier(m), 4),
            }

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

    scored_items = _apply_supersedes_order(scored_items, breakdowns)
    candidates = scored_items[:fetch_n]

    # Step 5: Reranker（可选）
    if use_rerank and settings.RERANKER_ENABLED and candidates:
        docs = [m.content for _, m in candidates]
        reranked = rerank(query, docs, top_k)
        if reranked:
            doc_to_m = {m.content: m for _, m in candidates}
            reranked_scored = [
                (r["score"], doc_to_m[r["document"]])
                for r in reranked if r["document"] in doc_to_m
            ]
            reranked_scored = _apply_supersedes_order(reranked_scored, breakdowns)
            results = []
            for s, m in reranked_scored:
                item = {"score": s, "document": m.content}
                if explain:
                    item["explain"] = breakdowns.get(m.id)
                results.append(item)
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

    results = []
    for s, m in candidates[:top_k]:
        item = {"score": s, "memory": m.model_dump(mode="json")}
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
    from datetime import timezone
    from lantai.core.time import utcnow
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
    return temporally_valid


def _age_multiplier(m) -> float:
    """按衰减类计算 decay_multiplier（调试字段；procedural 恒 1.0）。"""
    from datetime import timezone as _tz
    from lantai.core.time import utcnow as _utcnow
    from lantai.memory.decay_class import decay_multiplier as _dm
    last = m.last_used_at or m.created_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=_tz.utc)
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
) -> list[dict] | tuple[list[dict], list[dict]]:
    """向量检索降级路径：FTS5 召回作候选集，BM25 + decay 打分（无向量分）。

    权重归一化到 1：只使用 (W_BM25 + W_FTS + W_DECAY)。
    不触发 reranker——embedding 已不可用，rerank 大概率同样失败，保持降级可用。
    """
    # FTS5 子串召回作候选集（含追加语义：FTS 命中即候选）
    candidate_ids: set[str] = set()
    try:
        with db.get_session() as s:
            candidate_ids = set(search_fts(
                s.connection().connection.driver_connection,
                query, top_k=max(fetch_n, settings.FTS_RECALL_TOP_K)))
    except Exception:
        candidate_ids = set()

    # 拾遗（ADR-0028）：当 FTS5 无命中（如查询 <3 字符无法触发 trigram）时，
    # 采用 SQLite LIKE 短子串匹配候选，再由 BM25 + decay 排序
    if not candidate_ids:
        clean_q = query.strip()
        if len(clean_q) >= 2:
            try:
                with db.get_session() as s:
                    like_items = s.exec(
                        select(MemoryItem.id)
                        .where(MemoryItem.status == "active")
                        .where(MemoryItem.content.like(f"%{clean_q}%"))
                        .limit(max(fetch_n, settings.FTS_RECALL_TOP_K))
                    ).all()
                    candidate_ids = set(like_items)
            except Exception:
                candidate_ids = set()

    if not candidate_ids:
        if trace:
            return [], trace_steps
        return []

    with db.get_session() as s:
        items = s.exec(select(MemoryItem)
                       .where(MemoryItem.id.in_(candidate_ids),
                              MemoryItem.status == "active")).all()

    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    elif not settings.VERBATIM_IN_RECALL:
        # 原文直存默认不进混合召回（Ticket 02）：GET /verbatim/search 专用通道可查
        items = [m for m in items if m.memory_type != "verbatim"]
    if lanes:
        items = [m for m in items if m.lane in lanes]
    items = _chronos_filter(items)

    if not items:
        if trace:
            return [], trace_steps
        return []

    # BM25 + decay 融合（FTS 命中恒 1.0：候选集全部来自 FTS）
    bm25 = _get_bm25(items)
    bm_scores = bm25.get_scores(jieba.lcut(query))
    bm_range = bm_scores.max() - bm_scores.min()
    bm_norm = (bm_scores - bm_scores.min()) / (bm_range + 1e-8)
    total_w = (settings.RETRIEVAL_W_BM25
               + settings.RETRIEVAL_W_FTS
               + settings.RETRIEVAL_W_DECAY)

    scored = []
    breakdowns: dict[str, dict] = {}
    for i, m in enumerate(items):
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = settings.LANE_RETRIEVAL_BOOST.get(lane, 1.0)
        bm_val = float(bm_norm[i])
        score = (
            (settings.RETRIEVAL_W_BM25 * bm_val
             + settings.RETRIEVAL_W_FTS * 1.0
             + settings.RETRIEVAL_W_DECAY * m.decay_score)
            / total_w
        ) * lane_boost
        scored.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": 0.0,  # 降级路径无向量分
                "bm25": round(settings.RETRIEVAL_W_BM25 * bm_val / total_w, 4),
                "fts": round(settings.RETRIEVAL_W_FTS / total_w, 4),
                "decay": round(settings.RETRIEVAL_W_DECAY * m.decay_score / total_w, 4),
                "lane_boost": lane_boost,
                "final": round(score, 4),
                "decay_class": m.decay_class,
                # decay_multiplier 是 decay_class 理论半衰期参考（0.5^(age/hl)）；
                # 实际打分中的 decay 分项用的是 forgetting 的 lane-strength 指数衰减
                # （m.decay_score），两者不同源，decay_multiplier 仅供观测。
                "decay_multiplier": round(_age_multiplier(m), 4),
            }

    scored = _apply_supersedes_order(scored, breakdowns)
    results = []
    for s, m in scored[:top_k]:
        item = {"score": s, "memory": m.model_dump(mode="json")}
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
