"""遗忘质量自测（发展方向调研「一年内」档）：真实遗忘机制 + 检索的维度化评估。

对齐行业空白：写入精度 / 遗忘质量 / 时效 无公共基准——本模块把兰台最强能力
（Ebbinghaus 归档、Chronos 双时间轴、FTS trigram 中文容错）变成可复现的数字主张。

指标（诚实原则：无数据返回 0.0，绝不编造）：
- stale_hit_rate             陈旧记忆残留率（已归档记忆仍被召回，越低越好）
- typo_recall_rate           中文错别字容错命中率（FTS trigram 兜底，越高越好）
- fresh_recall_rate          对照组召回率（管道自检，应≈1）
- temporal_order_accuracy    时效排序正确率（未生效过滤 / 过期降权后新值在前）
- superseded_order_accuracy  被取代记忆排序正确率（新值在前）
- superseded_residual_rate   被取代记忆残留率（旧值仍出现在 top-k，越低越好）

evaluate_forgetting_quality：真实 DB 种子（namespace='eval_fq'）→ 真实检索
（search 可注入，默认 hybrid_search；外部 LLM/embedding/向量由调用方按测试纪律 mock）
→ 指标 → finally 清理种子。
"""
from datetime import timedelta

from sqlmodel import delete, select

from lantai.core.ids import new_id
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem
from lantai.storage import db
from lantai.storage.fts import sync_fts

EVAL_NAMESPACE = "eval_fq"

_CATEGORIES = ("stale", "typo", "fresh", "temporal", "superseded")


def compute_forgetting_metrics(per_query: list[dict]) -> dict:
    """纯函数：从 per-query 结果聚合六项指标（零 DB，可单测）。

    per_query 条目契约：
        {"category", "query", "result_ids": [...],
         "target_id": str|None, "forbidden_ids": [...],
         "preferred_id": str|None, "peer_id": str|None}
    - target_id 命中 = 期望召回的记忆出现在结果中
    - forbidden_ids 出现 = 期望不召回的（已归档/未生效）记忆残留
    - preferred_id/peer_id = 新值 vs 旧值（时效/取代），preferred 应排 peer 之前
    """
    def _rate(qs, hit_fn):
        if not qs:
            return 0.0
        return round(sum(1 for q in qs if hit_fn(q)) / len(qs), 4)

    def _hit_target(q):
        return q.get("target_id") in (q.get("result_ids") or [])

    def _stale_hit(q):
        forb = set(q.get("forbidden_ids") or [])
        return bool(forb & set(q.get("result_ids") or []))

    def _order_ok(q):
        ids = list(q.get("result_ids") or [])
        pref, peer = q.get("preferred_id"), q.get("peer_id")
        if not pref or pref not in ids:
            return False
        if peer not in ids:
            return True  # peer 被完全过滤 = 更优
        return ids.index(pref) < ids.index(peer)

    def _residual(q):
        return q.get("peer_id") in (q.get("result_ids") or [])

    stale = [q for q in per_query if q["category"] == "stale"]
    typo = [q for q in per_query if q["category"] == "typo"]
    fresh = [q for q in per_query if q["category"] == "fresh"]
    temporal = [q for q in per_query if q["category"] == "temporal"]
    superseded = [q for q in per_query if q["category"] == "superseded"]

    return {
        "sample_count": len(per_query),
        "stale_hit_rate": _rate(stale, _stale_hit),
        "typo_recall_rate": _rate(typo, _hit_target),
        "fresh_recall_rate": _rate(fresh, _hit_target),
        "temporal_order_accuracy": _rate(temporal, _order_ok),
        "superseded_order_accuracy": _rate(superseded, _order_ok),
        "superseded_residual_rate": _rate(superseded, _residual),
    }


def _seed_case(s, case: dict, now) -> dict[str, str]:
    """写一条 case 的记忆种子与关系边，返回 {seed_index(str): memory_id}。"""
    mapping: dict[str, str] = {}
    for i, seed in enumerate(case.get("seeds", [])):
        created = now - timedelta(days=seed.get("created_days_ago", 0))
        mem = MemoryItem(
            id=new_id("mem"),
            memory_type=seed.get("memory_type", "semantic"),
            namespace=EVAL_NAMESPACE,
            key=seed.get("key") or new_id("key"),
            content=seed["content"],
            lane=seed.get("lane", "general"),
            tier=seed.get("tier", "long_term"),
            confidence=1.0,
            importance=seed.get("importance", 0.5),
            decay_class=seed.get("decay_class", "episodic"),
            valid_from=(
                now + timedelta(days=seed["valid_from_days"])
                if "valid_from_days" in seed else None),
            valid_to=(
                now - timedelta(days=seed["valid_to_days"])
                if "valid_to_days" in seed else None),
            created_at=created,
            updated_at=created,
            last_used_at=seed.get("last_used_at") or created,
        )
        s.add(mem)
        s.flush()  # 同一事务内写 FTS（ADR-0008 强一致）
        sync_fts(s, mem.id, mem.content)
        mapping[str(i)] = mem.id
    for edge in case.get("edges", []):
        s.add(MemoryEdge(
            id=new_id("edge"),
            source_memory_id=mapping[str(edge["source"])],
            target_memory_id=mapping[str(edge["target"])],
            relation="supersedes",
            confidence=1.0,
        ))
    return mapping


def _collect_ids(results) -> list[str]:
    """从 hybrid_search 结果提取记忆 id（兼容 memory/document 两种形态）。"""
    ids: list[str] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        if "memory" in r:
            m = r["memory"]
            if isinstance(m, dict) and m.get("id"):
                ids.append(m["id"])
            elif hasattr(m, "id"):
                ids.append(m.id)
        elif "document" in r:
            ids.append(f"doc:{r['document'][:40]}")
    return ids


def evaluate_forgetting_quality(dataset: dict, *, search=None, top_k: int = 5) -> dict:
    """运行遗忘质量自测：种子 → （可选）遗忘 → 逐 case 检索 → 指标 → 清理。"""
    from lantai.memory.forgetting import apply_forgetting
    from lantai.retrieval.hybrid import delete_memory_item, hybrid_search

    run_search = search or hybrid_search
    now = utcnow()
    seeded: list[str] = []
    case_maps: list[dict] = []

    with db.get_session() as s:
        for case in dataset.get("cases", []):
            mapping = _seed_case(s, case, now)
            seeded.extend(mapping.values())
            case_maps.append(mapping)
        s.commit()

    try:
        if dataset.get("apply_forgetting"):
            apply_forgetting()

        per_query: list[dict] = []
        for idx, case in enumerate(dataset.get("cases", [])):
            mapping = case_maps[idx]
            results = run_search(case["query"], top_k=top_k, use_rerank=False)
            result_ids = _collect_ids(results)
            per_query.append({
                "category": case["category"],
                "query": case["query"],
                "result_ids": result_ids,
                "target_id": mapping.get(str(case["target"]))
                if case.get("target") is not None else None,
                "forbidden_ids": [mapping[str(i)] for i in case.get("forbidden", [])],
                "preferred_id": mapping.get(str(case["preferred"]))
                if case.get("preferred") is not None else None,
                "peer_id": mapping.get(str(case["peer"]))
                if case.get("peer") is not None else None,
            })
        return {
            "dataset": dataset.get("name", ""),
            "metrics": compute_forgetting_metrics(per_query),
            "per_query": per_query,
        }
    finally:
        # 同一事务内删边 + 删记忆本体 + 删 FTS 索引（杜绝跨 session 残留）
        with db.get_session() as s:
            if seeded:
                s.exec(delete(MemoryEdge).where(
                    MemoryEdge.source_memory_id.in_(seeded) |
                    MemoryEdge.target_memory_id.in_(seeded)))
                s.exec(delete(MemoryItem).where(MemoryItem.id.in_(seeded)))
                for mid in seeded:
                    sync_fts(s, mid, None)
                s.commit()
        # 向量库独立清理（失败静默，不影响 DB 清理结论）
        for mid in seeded:
            try:
                delete_memory_item(mid)
            except Exception:
                pass
