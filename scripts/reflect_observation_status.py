"""只读检查反思观察期的滑动窗口合格定时运行（ADR-0027 察窗）。

从 v0.16 起改为窗口内计数：最近 REFLECT_OBSERVATION_WINDOW_DAYS 天内
至少 REFLECT_OBSERVATION_REQUIRED_RUNS 天有合格 scheduled 运行。
默认报告当前状态；--check 仅在达到窗口门槛时返回 0，供发布准备使用。
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

from lantai.core.settings import settings  # noqa: E402

def _is_success(row: sqlite3.Row) -> bool:
    return not row["error"] and not row["curate_failed"] and not row["rejecter_failed"]

def collect_observation_status(conn: sqlite3.Connection, required_runs: int = 7,
                              window_days: int = 14,
                              reference_date: date | None = None) -> dict:
    """真实 SQLite 记录直读：滑动窗口内计数合格 scheduled 运行天数（ADR-0027 察窗）。

    最近 window_days 天内（以 reference_date 或今日为基准），统计至少有一天合格 scheduled 运行的天数。
    同一日内多次运行中任一合格即算该日合格。
    """
    if required_runs < 1:
        raise ValueError("required_runs must be positive")
    if window_days < required_runs:
        raise ValueError("window_days must be >= required_runs")

    conn.row_factory = sqlite3.Row
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reflect_run)")}
    if "source" not in columns:
        return {
            "required_runs": required_runs,
            "window_days": window_days,
            "qualified_days": 0,
            "remaining_runs": required_runs,
            "ready": False,
            "latest_scheduled_day": "",
            "source_counts": {"scheduled": 0, "manual": 0, "unknown": 0},
            "migration_required": True,
        }
    rows = conn.execute(
        "SELECT run_at, source, curate_failed, rejecter_failed, error "
        "FROM reflect_run ORDER BY run_at DESC").fetchall()
    by_day: dict[object, list[sqlite3.Row]] = defaultdict(list)
    source_counts = {"scheduled": 0, "manual": 0, "unknown": 0}
    for row in rows:
        source = row["source"] or "unknown"
        source_counts[source if source in source_counts else "unknown"] += 1
        if source == "scheduled":
            by_day[datetime.fromisoformat(row["run_at"]).date()].append(row)

    # 滑动窗口：从基准日（默认今日）往前数 window_days 天，计合格天数
    today = reference_date or datetime.now().date()
    window_start = today - timedelta(days=window_days - 1)
    qualified = 0
    latest_scheduled = ""
    for day in sorted(by_day, reverse=True):
        if day < window_start:
            break
        if day > today:
            continue
        if any(_is_success(row) for row in by_day[day]):
            qualified += 1
        if not latest_scheduled:
            latest_scheduled = day.isoformat()

    latest_day = max(by_day).isoformat() if by_day else ""
    return {
        "required_runs": required_runs,
        "window_days": window_days,
        "qualified_days": qualified,
        "remaining_runs": max(required_runs - qualified, 0),
        "ready": qualified >= required_runs,
        "latest_scheduled_day": latest_day,
        "source_counts": source_counts,
        "migration_required": False,
    }

def render_status(status: dict) -> str:
    state = "PASS" if status["ready"] else "PENDING"
    if status["migration_required"]:
        return "\n".join([
            "反思观察期状态",
            "门禁：PENDING",
            "原因：数据库尚未迁移至 schema v13（reflect_run.source 缺失）",
            "请由服务启动流程完成迁移后再检查。",
        ]) + "\n"
    latest = status["latest_scheduled_day"] or "暂无"
    counts = status["source_counts"]
    window = status.get("window_days", 14)
    return "\n".join([
        "反思观察期状态",
        f"门禁：{state}",
        f"窗口内合格定时运行天数：{status['qualified_days']}/{status['required_runs']}（{window} 天窗口）",
        f"剩余：{status['remaining_runs']}",
        f"最近定时运行日：{latest}",
        "来源记录："
        f"scheduled={counts['scheduled']} manual={counts['manual']} unknown={counts['unknown']}",
    ]) + "\n"

def _database_path() -> Path:
    prefix = "sqlite:///"
    if not settings.DATABASE_URL.startswith(prefix):
        raise ValueError("reflect observation status requires a SQLite database")
    return Path(settings.DATABASE_URL[len(prefix):])

def main(argv: list[str] | None = None, database_path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="反思观察期状态（只读）")
    parser.add_argument("--check", action="store_true", help="未达窗口门槛时返回失败码")
    parser.add_argument("--required", type=int, default=None, help="窗口内合格天数门槛（默认 settings）")
    parser.add_argument("--window", type=int, default=None, help="滑动窗口大小（天，默认 settings）")
    args = parser.parse_args(argv)

    required = args.required if args.required is not None else settings.REFLECT_OBSERVATION_REQUIRED_RUNS
    window = args.window if args.window is not None else settings.REFLECT_OBSERVATION_WINDOW_DAYS

    path = database_path or _database_path()
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        status = collect_observation_status(conn, required, window)
    sys.stdout.write(render_status(status))
    return 0 if not args.check or status["ready"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
