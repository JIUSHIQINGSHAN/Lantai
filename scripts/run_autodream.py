"""autodream 蒸馏 CLI：规则聚类 + 确定性合并 → 待审提案（人工闸门裁决）。

用法：
    python scripts/run_autodream.py --dry-run           # 只规划不写库（默认）
    python scripts/run_autodream.py --apply             # 落 pending 提案（≤ AUTODREAM_MAX_DAILY）
    python scripts/run_autodream.py --namespace default --limit 200
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lantai.evolution.autodream import plan_distillation, run_autodream_once  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆 autodream 蒸馏")
    ap.add_argument("--apply", action="store_true",
                    help="落 pending 提案（默认 dry-run 只规划不写库）")
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="只输出 JSON 汇总")
    args = ap.parse_args()

    result = run_autodream_once(args.namespace, dry_run=not args.apply,
                                limit=args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"autodream 蒸馏（{'dry-run' if not args.apply else 'apply'}）: "
          f"clusters={result['clusters']} plans={result['plans']} "
          f"created={result['created']}")
    if result["skipped"]:
        print("skipped:", ", ".join(result["skipped"]))
    if not args.apply:
        print("\n（dry-run 仅规划；--apply 才落 pending 提案，应用交人工闸门）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())