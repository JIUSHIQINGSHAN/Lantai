"""沉潜过审验收统计报告 CLI（ADR-0050 / 票 07 交付 5）。

用法：
    PYTHONPATH=. python scripts/consolidation_audit_report.py               # 近 7 天
    PYTHONPATH=. python scripts/consolidation_audit_report.py --days 30     # 近 30 天

口径（ADR-0050 决策 9）：
- enforce 自证①②合取：提案数÷purified_ok 恰 100% ＋ 窗口内伪 id checkpoint 新增行数 0；
- shadow 三方互证：伪 id checkpoint 行数 == 影子提案数 == purified_ok；
- 无样本时比例返回 None 不编造。
"""

import json
import sys

from lantai.services.consolidation_service import consolidation_audit_report


def main() -> int:
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])
    report = consolidation_audit_report(window_days=days)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
