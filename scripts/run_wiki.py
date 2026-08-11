"""记忆 Wiki 刷新 CLI：场景/技能 → 页面 → index → overview（mem_sync 同款入口）。

用法：
    python scripts/run_wiki.py                # 刷新 wiki（LLM 综述按设置）
    python scripts/run_wiki.py --no-llm       # 强制确定性综述（不调 LLM）
    python scripts/run_wiki.py --json         # 只输出 JSON 汇总
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lantai.services.wiki_service import run_wiki_update_once  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆 Wiki 刷新")
    ap.add_argument("--no-llm", action="store_true",
                    help="强制确定性综述（不调 LLM）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 汇总")
    args = ap.parse_args()

    result = run_wiki_update_once(overview_llm=not args.no_llm)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"记忆 Wiki 已刷新: {result['dir']}")
    print(f"页面 {result['pages']} 个（过期清理 {result['stale_removed']} 个），"
          f"overview={result['overview']}，耗时 {result['took_ms']}ms")
    return 0


if __name__ == "__main__":
    main()