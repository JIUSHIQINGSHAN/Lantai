from typing import Any, Optional, Mapping
from dataclasses import dataclass, field
import threading
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


# BM25 语料缓存（M4）：key = items 的 (id, content) 有序元组，线程锁保护（F3）
_BM25_LOCK = threading.Lock()
_BM25_CACHE: dict = {"key": None, "bm25": None}


def _get_bm25(items: list) -> "BM25Okapi":
    key = tuple((m.id, m.content) for m in items)
    with _BM25_LOCK:
        if _BM25_CACHE.get("key") == key:
            return _BM25_CACHE["bm25"]
    corpus = [jieba.lcut(m.content) for m in items]
    bm25 = BM25Okapi(corpus)
    with _BM25_LOCK:
        _BM25_CACHE["key"] = key
        _BM25_CACHE["bm25"] = bm25
    return bm25


def _apply_supersedes_order(scored: list, breakdowns: dict | None = None,
                            params: RetrievalParams | None = None) -> list:
    """supersedes 边感知降权：被取代旧值若其新值同在候选集，压到新值之下。

    宁 miss 不脏写：不删除旧值（残留如实留在结果中），仅保证新值在前；
    新值不在候选集时不动旧值（有旧值可用总比空手好）。仅按候选 id 做一次
    边查找，异常/缺边静默降级为原排序。
    """
    p = params or RetrievalParams()
    if not p.supersedes_enabled or not scored:
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

    # 从 SQLite 加载完整记忆项——仅 active
    ids = [r["id"] for r in vector_results]

    def _query_items(s):
        return s.exec(
            select(MemoryItem)
            .where(MemoryItem.id.in_(ids))
            .where(MemoryItem.status == "active")
        ).all()

    if session is not None:
        items = _query_items(session)
    else:
        with db.get_session() as s:
            items = _query_items(s)
    items_by_id = {m.id: m for m in items}

    # 过滤 memory_types / lanes / domain
    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    elif not p.verbatim_in_recall:
        # 原文直存默认不进混合召回（Ticket 02）：GET /verbatim/search 专用通道可查
        items = [m for m in items if m.memory_type != "verbatim"]
    if lanes:
        items = [m for m in items if m.lane in lanes]
    if domain and domain != "all":
        items = [m for m in items if getattr(m, "domain", "user") == domain]


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
                query, top_k=p.fts_recall_top_k))
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
        lane_boost = p.lane_boost.get(lane, 1.0)
        persona_boost = 1.05 if lane in ("preference", "rule") else 1.0
        fts_hit = 1.0 if m.id in fts_hits else 0.0
        bm_val = float(bm_norm[i])
        score = (p.w_vector * vs
                 + p.w_bm25 * bm_val
                 + p.w_fts * fts_hit
                 + p.w_decay * m.decay_score) * lane_boost * persona_boost
        scored_items.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": round(p.w_vector * vs, 4),
                "bm25": round(p.w_bm25 * bm_val, 4),
                "fts": round(p.w_fts * fts_hit, 4),
                "decay": round(p.w_decay * m.decay_score, 4),
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
                (p.w_fts + p.w_decay * m.decay_score, m))

    scored_items = _apply_supersedes_order(scored_items, breakdowns, params=p)
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
    domain: str | None = None,
    session: Any = None,
    params: RetrievalParams | None = None,
) -> list[dict] | tuple[list[dict], list[dict]]:
    """向量检索降级路径：FTS5 召回作候选集，BM25 + decay 打分（无向量分）。

    权重归一化到 1：只使用 (W_BM25 + W_FTS + W_DECAY)。
    不触发 reranker——embedding 已不可用，rerank 大概率同样失败，保持降级可用。
    """
    p = params or RetrievalParams()
    # FTS5 子串召回作候选集（含追加语义：FTS 命中即候选）
    candidate_ids: set[str] = set()

    def _get_s(cb):
        if session is not None:
            return cb(session)
        with db.get_session() as s:
            return cb(s)

    try:
        def _fts_cb(s):
            return search_fts(
                s.connection().connection.driver_connection,
                query, top_k=max(fetch_n, p.fts_recall_top_k))
        candidate_ids = set(_get_s(_fts_cb))
    except Exception:
        candidate_ids = set()

    # 拾遗（ADR-0028）：当 FTS5 无命中（如查询 <3 字符无法触发 trigram）时，
    # 采用 SQLite LIKE 短子串匹配候选，再由 BM25 + decay 排序
    if not candidate_ids:
        clean_q = query.strip()
        tokens = [w.strip() for w in jieba.lcut(clean_q) if len(w.strip()) >= 1]
        if tokens:
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
                candidate_ids = set(like_items)
            except Exception:
                candidate_ids = set()


    if not candidate_ids:
        if trace:
            return [], trace_steps
        return []

    def _items_cb(s):
        return s.exec(select(MemoryItem)
                       .where(MemoryItem.id.in_(candidate_ids),
                              MemoryItem.status == "active")).all()

    items = _get_s(_items_cb)

    if memory_types:
        items = [m for m in items if m.memory_type in memory_types]
    elif not p.verbatim_in_recall:
        # 原文直存默认不进混合召回（Ticket 02）：GET /verbatim/search 专用通道可查
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

    # BM25 + decay 融合（FTS 命中恒 1.0：候选集全部来自 FTS）
    bm25 = _get_bm25(items)
    bm_scores = bm25.get_scores(jieba.lcut(query))
    bm_range = bm_scores.max() - bm_scores.min()
    bm_norm = (bm_scores - bm_scores.min()) / (bm_range + 1e-8)
    total_w = (p.w_bm25 + p.w_fts + p.w_decay)

    scored = []
    breakdowns: dict[str, dict] = {}
    for i, m in enumerate(items):
        lane = getattr(m, "lane", "general") or "general"
        lane_boost = p.lane_boost.get(lane, 1.0)
        bm_val = float(bm_norm[i])
        score = (
            (p.w_bm25 * bm_val
             + p.w_fts * 1.0
             + p.w_decay * m.decay_score)
            / total_w
        ) * lane_boost
        scored.append((score, m))
        if explain:
            breakdowns[m.id] = {
                "vector": 0.0,  # 降级路径无向量分
                "bm25": round(p.w_bm25 * bm_val / total_w, 4),
                "fts": round(p.w_fts / total_w, 4),
                "decay": round(p.w_decay * m.decay_score / total_w, 4),
                "lane_boost": lane_boost,
                "final": round(score, 4),
                "decay_class": m.decay_class,
                # decay_multiplier 是 decay_class 理论半衰期参考（0.5^(age/hl)）；
                # 实际打分中的 decay 分项用的是 forgetting 的 lane-strength 指数衰减
                # （m.decay_score），两者不同源，decay_multiplier 仅供观测。
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
