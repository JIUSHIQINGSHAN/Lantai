"""只读检查反思观察期的连续合格定时运行。

默认报告当前状态；--check 仅在达到连续门槛时返回 0，供发布准备使用。
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

from lantai.core.settings import settings  # noqa: E402


def _is_success(row: sqlite3.Row) -> bool:
    return not row["error"] and not row["curate_failed"] and not row["rejecter_failed"]


def collect_observation_status(conn: sqlite3.Connection, required_runs: int = 7) -> dict:
    """真实 SQLite 记录直读：只统计来源明确且连续成功的定时运行。"""
    if required_runs < 1:
        raise ValueError("required_runs must be positive")

    conn.row_factory = sqlite3.Row
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reflect_run)")}
    if "source" not in columns:
        return {
            "required_runs": required_runs,
            "consecutive_successes": 0,
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

    streak = 0
    previous_day = None
    for day in sorted(by_day, reverse=True):
        if previous_day is not None and previous_day - day != timedelta(days=1):
            break
        if not all(_is_success(row) for row in by_day[day]):
            break
        streak += 1
        previous_day = day

    latest_day = max(by_day).isoformat() if by_day else ""
    return {
        "required_runs": required_runs,
        "consecutive_successes": streak,
        "remaining_runs": max(required_runs - streak, 0),
        "ready": streak >= required_runs,
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
    return "\n".join([
        "反思观察期状态",
        f"门禁：{state}",
        f"连续合格定时运行：{status['consecutive_successes']}/{status['required_runs']}",
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
    parser.add_argument("--check", action="store_true", help="未达连续门槛时返回失败码")
    parser.add_argument("--required", type=int, default=7, help="连续定时成功门槛（默认 7）")
    args = parser.parse_args(argv)

    path = database_path or _database_path()
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        status = collect_observation_status(conn, args.required)
    sys.stdout.write(render_status(status))
    return 0 if not args.check or status["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
