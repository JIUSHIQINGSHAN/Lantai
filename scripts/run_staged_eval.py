"""E1 阶段化评测 CLI（票 03 交付 3）：输出每段一行报告。

用法：
    PYTHONPATH=. python scripts/run_staged_eval.py            # 跑六段回放并打印
    PYTHONPATH=. python scripts/run_staged_eval.py --latest   # 打印最近一次 staged 结果

不 mock 边界：CLI 直连部署库与真实 embed/向量库（生产路径）。
"""

import sys

from lantai.eval.staged import format_staged_report, run_staged_eval


def main() -> int:
    result = run_staged_eval()
    print(format_staged_report(result))

    anchors_ok = all(a["achieved"] for a in result["anchors"])
    stages_complete = len(result["stages"]) == 6 and all(
        "samples" in st and "success_rate" in st for st in result["stages"]
    )
    print()
    print(f"六段齐全: {stages_complete} | 预埋锚点全归位: {anchors_ok}")
    return 0 if (anchors_ok and stages_complete) else 1


if __name__ == "__main__":
    sys.exit(main())
