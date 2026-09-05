"""观察期回填校准 CLI：从真实 DB 拉窗口内反思分布，输出二次校准 markdown。

用法：
    python scripts/calibrate_reflection.py            # 最近 7 天（观察期窗口）
    python scripts/calibrate_reflection.py --days 14
    python scripts/calibrate_reflection.py > docs/memory-quality/reflect-calibration-YYYY-MM-DD.md
"""
import argparse
import sys

sys.path.insert(0, ".")

from lantai.storage.db import init_db  # noqa: E402
from lantai.workers.digest_worker import (  # noqa: E402
    collect_calibration_stats,
    render_calibration_markdown,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="反思阈值回填校准（真实观察数据）")
    ap.add_argument("--days", type=int, default=7, help="观察窗口天数（默认 7）")
    args = ap.parse_args()

    init_db()  # 幂等：CLI 直跑时保证增量迁移已应用（老库缺新列不报错）
    stats = collect_calibration_stats(days=args.days)
    sys.stdout.write(render_calibration_markdown(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
