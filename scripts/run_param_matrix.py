"""调参对比矩阵：批量跑 dry-run 多组参数，输出位置敏感指标对比表。

用法（Windows 本机 .venv-audit）：
    python scripts/run_param_matrix.py --query-set dry-run-v1 --intent rule --no-rerank
    python scripts/run_param_matrix.py --limit 30            # 快速试跑
    python scripts/run_param_matrix.py --baseline <run_id>   # 指定基线 run

输出：
    - 终端打印矩阵表（每组参数的 zero/avg/top1_consist/top3_consist/pos_drift/jaccard）
    - 结果写入 docs/param-matrix-report.md
    - 每组一个 eval_run 落库（param_overrides 标记），可与 --baseline 比 jaccard

背景：dry-run v1 报告用 jaccard 判断"权重不敏感"是盲区——
jaccard 用 set 比较，忽略排序变化。本脚本补位置敏感指标：
top1 一致率 / top3 集合一致率 / 平均位置漂移。
见 docs/param-matrix-report.md 实证：W_VECTOR 0.6->0.75 时 14/179 条 top1 改变。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from lantai.core.settings import settings  # noqa: E402
from lantai.eval.metrics import compute_metrics  # noqa: E402
from lantai.eval.query_set import load_query_set  # noqa: E402
from lantai.eval.runner import run_dry_run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# 矩阵：每组一个标签 + param_overrides（完整权重元组，保证归一化可比）
DEFAULT_WEIGHTS = {
    "RETRIEVAL_W_VECTOR": settings.RETRIEVAL_W_VECTOR,
    "RETRIEVAL_W_BM25": settings.RETRIEVAL_W_BM25,
    "RETRIEVAL_W_FTS": settings.RETRIEVAL_W_FTS,
    "RETRIEVAL_W_DECAY": settings.RETRIEVAL_W_DECAY,
}

MATRIX = [
    ("base", {}),                                   # 默认权重基线
    ("vec+", {"RETRIEVAL_W_VECTOR": 0.75, "RETRIEVAL_W_BM25": 0.15}),
    ("vec++", {"RETRIEVAL_W_VECTOR": 0.85, "RETRIEVAL_W_BM25": 0.05}),
    ("bm25+", {"RETRIEVAL_W_BM25": 0.45, "RETRIEVAL_W_VECTOR": 0.40}),
    ("decay+", {"RETRIEVAL_W_DECAY": 0.25, "RETRIEVAL_W_VECTOR": 0.45, "RETRIEVAL_W_BM25": 0.20}),
    ("fts+", {"RETRIEVAL_W_FTS": 0.15, "RETRIEVAL_W_VECTOR": 0.50, "RETRIEVAL_W_BM25": 0.25}),
]


def _overrides(base: dict, delta: dict) -> dict:
    """合并默认权重与 delta；仅返回 delta（run_dry_run 会在 snapshot 上叠加）。"""
    return dict(delta)


def position_sensitive_metrics(default_pq: list[dict], ov_pq: list[dict]) -> dict:
    """位置敏感指标：top1/top3 一致率 + 平均位置漂移 + top_scores 相关。

    均为纯函数，零 DB 依赖；与 compute_metrics 互补（那个只看集合）。
    """
    n = min(len(default_pq), len(ov_pq))
    top1_same = 0
    top3_set_same = 0
    pos_drift = 0.0
    pos_pairs = 0
    scores_a, scores_b = [], []
    for a, b in zip(default_pq[:n], ov_pq[:n]):
        ai, bi = a.get("result_ids") or [], b.get("result_ids") or []
        if ai and bi and ai[0] == bi[0]:
            top1_same += 1
        if ai[:3] and set(ai[:3]) == set(bi[:3]):
            top3_set_same += 1
        bpos = {x: i for i, x in enumerate(bi)}
        for i, x in enumerate(ai):
            if x in bpos:
                pos_drift += abs(i - bpos[x])
                pos_pairs += 1
        sa, sb = a.get("top_scores") or [], b.get("top_scores") or []
        k = min(len(sa), len(sb))
        scores_a += sa[:k]
        scores_b += sb[:k]
    corr = _pearson(scores_a, scores_b) if len(scores_a) > 2 else None
    return {
        "top1_consistency": round(top1_same / n, 4) if n else None,
        "top3_set_consistency": round(top3_set_same / n, 4) if n else None,
        "avg_pos_drift": round(pos_drift / max(pos_pairs, 1), 4),
        "score_corr": round(corr, 4) if corr is not None else None,
    }


def _pearson(x: list[float], y: list[float]) -> float:
    import math
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    if not vx or not vy:
        return 0.0
    return cov / (vx * vy)


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆 调参对比矩阵")
    ap.add_argument("--query-set", default="dry-run-v1")
    ap.add_argument("--intent", choices=["llm", "rule"], default="rule")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-write", action="store_true", help="不写报告，仅打印")
    args = ap.parse_args()

    qs = load_query_set(args.query_set)
    if qs is None:
        print(f"查询集不存在: {args.query_set}（先用 build_query_set 创建）", file=sys.stderr)
        return 1
    if args.limit and 0 < args.limit < len(qs.queries or []):
        qs.queries = (qs.queries or [])[:args.limit]
        qs.sample_count = len(qs.queries)

    print(f"=== 调参对比矩阵: {qs.name}（{qs.sample_count} 样本, top_k={args.top_k}, "
          f"rerank={not args.no_rerank}, intent={args.intent}）===\n")

    results = []
    base_run_id = args.baseline
    for label, delta in MATRIX:
        t0 = time.perf_counter()
        run = run_dry_run(
            qs,
            param_overrides=delta or None,
            top_k=args.top_k,
            baseline_run_id=base_run_id,
            use_rerank=not args.no_rerank,
            intent_mode=args.intent,
        )
        elapsed = time.perf_counter() - t0
        m = run.metrics
        results.append({
            "label": label,
            "run_id": run.id,
            "overrides": delta or "default",
            "zero": m.get("zero_result_rate"),
            "avg": m.get("avg_result_count"),
            "jaccard": m.get("jaccard_vs_baseline"),
            "elapsed_s": round(elapsed, 1),
        })
        if label == "base":
            base_run_id = run.id  # 后续组以此基线比 jaccard
        print(f"[{label:>6}] run={run.id[:18]} zero={results[-1]['zero']} "
              f"avg={results[-1]['avg']} jaccard={results[-1]['jaccard']} "
              f"({results[-1]['elapsed_s']}s)")

    # —— 位置敏感对比：base vs 每组 ——
    from lantai.eval.models import EvalRun
    from lantai.storage import db
    base_pq = None
    with db.get_session() as s:
        base_row = s.get(EvalRun, results[0]["run_id"])
        base_pq = base_row.per_query if base_row else None

    print("\n=== 位置敏感对比（vs 基线轮）===")
    print(f"{'label':>6} | {'top1':>6} | {'top3set':>6} | {'posdrift':>8} | {'scorecorr':>9}")
    if base_pq:
        for r in results[1:]:
            with db.get_session() as s:
                ov_row = s.get(EvalRun, r["run_id"])
                if not ov_row:
                    continue
            ps = position_sensitive_metrics(base_pq, ov_row.per_query)
            r.update(ps)
            print(f"{r['label']:>6} | {ps['top1_consistency']:>6} | "
                  f"{ps['top3_set_consistency']:>6} | {ps['avg_pos_drift']:>8} | "
                  f"{ps['score_corr']:>9}")
    else:
        print("（无基线 per_query，跳过位置敏感对比）")

    if not args.no_write:
        _write_report(qs, results, base_pq, args)
        print(f"\n报告已写入 docs/param-matrix-report.md")
    return 0


def _write_report(qs, results, base_pq, args):
    lines = [
        f"# 调参对比矩阵报告",
        f"",
        f"> 日期：{time.strftime('%Y-%m-%d %H:%M')} | 查询集：{qs.name}（{qs.sample_count} 样本）",
        f"> 命令：`python scripts/run_param_matrix.py --query-set {qs.name} --intent {args.intent}"
        f" --no-rerank --top-k {args.top_k}`",
        f"",
        f"## 一、矩阵结果",
        f"",
        f"| 标签 | overrides | zero_rate | avg_count | jaccard | 耗时 |",
        f"|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['label']} | `{r['overrides']}` | {r['zero']} | {r['avg']} "
            f"| {r['jaccard']} | {r['elapsed_s']}s |")
    lines += [
        f"",
        f"## 二、位置敏感对比（vs 基线轮，Jaccard 盲区补充）",
        f"",
        f"| 标签 | top1一致率 | top3集合一致率 | 平均位置漂移 | 分数相关 |",
        f"|---|---|---|---|---|",
    ]
    if base_pq:
        for r in results[1:]:
            if "top1_consistency" not in r:
                continue
            lines.append(
                f"| {r['label']} | {r['top1_consistency']} | {r['top3_set_consistency']} "
                f"| {r['avg_pos_drift']} | {r['score_corr']} |")
    lines += [
        f"",
        f"## 三、解读",
        f"",
        f"- **jaccard 是集合指标，忽略排序**：top-k 结果集合不变不代表排序不变。",
        f"- **位置敏感指标**（top1/top3 一致率、位置漂移）才能暴露权重影响。",
        f"- 库量级小（当前 4 条记忆）时，结果集中在少数记忆上，集合几乎不变，",
        f"  但权重变化会改变排序——矩阵应同时看两类指标。",
        f"- 库量级涨上来后（>100 条记忆），jaccard 与位置敏感指标都会出现分化。",
        f"",
        f"## 附：run_id",
    ]
    for r in results:
        lines.append(f"- {r['label']}: `{r['run_id']}`")
    out = REPO_ROOT / "docs" / "param-matrix-report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
