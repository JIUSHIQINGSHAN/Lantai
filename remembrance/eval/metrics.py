"""评估指标：dry-run 相对指标纯函数。

约束（已钉死）：
- 全部纯函数，零 DB 依赖，输入输出可测
- 诚实原则：无数据时返回 None 或 0.0，绝不编造
- 接口契约见 docs/dry-run-eval-task-split.md
"""
from typing import Optional


def zero_result_rate(per_query: list[dict]) -> float:
    """零结果查询占比：result_ids 为空的查询数 / 总查询数。"""
    if not per_query:
        return 0.0
    zeros = sum(1 for q in per_query if q.get("zero_result") is True
                or not q.get("result_ids"))
    return zeros / len(per_query)


def avg_result_count(per_query: list[dict]) -> float:
    """平均返回结果数。"""
    if not per_query:
        return 0.0
    total = sum(len(q.get("result_ids") or []) for q in per_query)
    return total / len(per_query)


def jaccard_overlap(a: list[list[str]], b: list[list[str]]) -> float:
    """两轮运行同查询的召回集合 Jaccard 均值。

    a/b 是每查询的 result_ids 列表（按顺序对应同一查询）。
    空集合对记 0，全部空返回 0.0。只比较对齐到短者的前 N 个。
    """
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    total = 0.0
    valid = 0
    for i in range(n):
        sa = set(a[i])
        sb = set(b[i])
        if not sa and not sb:
            continue  # 双空，跳过不计
        valid += 1
        inter = len(sa & sb)
        union = len(sa | sb)
        total += inter / union if union else 0.0
    return total / valid if valid else 0.0


def weak_hit_rate(per_query: list[dict], *,
                  used_ids_map: Optional[dict[str, list[str]]] = None) -> float | None:
    """弱命中率：used_ids 在 top-k 结果中的比例。

    used_ids_map: {event_id: [used_id, ...]}（来自回填，无生成侧时缺省）
    无 used_ids 数据时返回 None（诚实标 unavailable，不编造 0）。
    """
    if not used_ids_map:
        return None
    if not per_query:
        return None
    hits = 0
    total = 0
    for q in per_query:
        eid = q.get("event_id") or ""
        used = used_ids_map.get(eid, [])
        if not used:
            continue  # 该事件无弱标注，跳过
        total += 1
        result_ids = set(q.get("result_ids") or [])
        if any(u in result_ids for u in used):
            hits += 1
    return hits / total if total else None


def compute_metrics(per_query: list[dict], *,
                    baseline_per_query: list[list[str]] | None = None,
                    used_ids_map: Optional[dict[str, list[str]]] = None) -> dict:
    """聚合全部指标。

    返回：
        {
            "sample_count": int,
            "zero_result_rate": float,
            "avg_result_count": float,
            "weak_hit_rate": float | None,        # 无 used_ids 数据时 None
            "jaccard_vs_baseline": float | None,  # 无 baseline 时 None
        }
    """
    if not per_query:
        return {
            "sample_count": 0,
            "zero_result_rate": 0.0,
            "avg_result_count": 0.0,
            "weak_hit_rate": None,
            "jaccard_vs_baseline": None,
        }

    current_result_ids = [q.get("result_ids") or [] for q in per_query]
    result = {
        "sample_count": len(per_query),
        "zero_result_rate": round(zero_result_rate(per_query), 4),
        "avg_result_count": round(avg_result_count(per_query), 4),
        "weak_hit_rate": (
            round(v, 4) if (v := weak_hit_rate(per_query, used_ids_map=used_ids_map)) is not None
            else None
        ),
        "jaccard_vs_baseline": None,
    }
    if baseline_per_query is not None:
        result["jaccard_vs_baseline"] = round(
            jaccard_overlap(current_result_ids, baseline_per_query), 4)
    return result
