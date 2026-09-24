"""回执链可追溯率报告 CLI（ADR-0049 / 票 04 交付 4）。

用法：
    PYTHONPATH=. python scripts/receipt_report.py                 # 当前统计
    PYTHONPATH=. python scripts/receipt_report.py --mark-missed   # 先跑超时判定再统计
"""

import json
import sys

from lantai.observability.retrieval_log import (
    mark_missed_receipts,
    receipt_traceability_report,
)


def main() -> int:
    if "--mark-missed" in sys.argv:
        moved = mark_missed_receipts()
        print(f"missed 置位: {moved} 事件")
    report = receipt_traceability_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
