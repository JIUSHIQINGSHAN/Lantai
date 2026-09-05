"""Dry-run 评估 CLI：跑一轮评估并打印指标报告。

用法：
    python scripts/run_dry_run.py --query-set dry-run-v1
    python scripts/run_dry_run.py --query-set dry-run-v1 --override RETRIEVAL_W_VECTOR=0.7 RETRIEVAL_W_BM25=0.15
    python scripts/run_dry_run.py --query-set dry-run-v1 --baseline <RUN_ID> --top-k 10
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lantai.eval.query_set import load_query_set  # noqa: E402
from lantai.eval.runner import run_dry_run  # noqa: E402


def parse_overrides(items: list[str]) -> dict:
    """解析 --override key=value 列表。"""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--override 需 key=value 格式，got: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = float(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆 dry-run 评估")
    ap.add_argument("--query-set", required=True, help="查询集名（build_query_set 创建）")
    ap.add_argument("--override", nargs="*", default=None,
                    help="参数覆盖 key=value（可多个）")
    ap.add_argument("--baseline", default=None, help="基线 EvalRun id（算 jaccard）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 条查询（快速验证管道用）")
    ap.add_argument("--intent", choices=["llm", "rule"], default="llm",
                    help="意图分类方式：llm=真实API（慢），rule=规则fallback（快，评估用）")
    args = ap.parse_args()

    qs = load_query_set(args.query_set)
    if qs is None:
        print(f"查询集不存在: {args.query_set}（先用 build_query_set 创建）", file=sys.stderr)
        return 1

    if args.limit and 0 < args.limit < len(qs.queries):
        qs.queries = qs.queries[:args.limit]
        qs.sample_count = len(qs.queries)

    overrides = parse_overrides(args.override)
    run = run_dry_run(
        qs,
        param_overrides=overrides or None,
        top_k=args.top_k,
        baseline_run_id=args.baseline,
        use_rerank=not args.no_rerank,
        intent_mode=args.intent,
    )

    print("\n=== Dry-Run 报告 ===")
    print(f"run_id      : {run.id}")
    print(f"查询集      : {run.query_set_name}（{qs.sample_count} 条样本）")
    print(f"top_k       : {args.top_k} | rerank: {not args.no_rerank} | intent: {args.intent}")
    print(f"参数覆盖    : {overrides or '(默认)'}")
    print(f"基线        : {args.baseline or '-'}")
    print(f"指标        : {json.dumps(run.metrics, ensure_ascii=False, indent=2)}")
    print(f"\n完整结果存于 eval_run 表（id={run.id}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
