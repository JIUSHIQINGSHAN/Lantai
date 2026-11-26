"""召回健康检查（只读）。

检查最近检索事件：最后检索时间、零召回率、系统噪音比例、检索趋势。
"""

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lantai.core.settings import settings  # noqa: E402


def _database_path() -> Path:
    prefix = "sqlite:///"
    url = settings.DATABASE_URL
    if not url.startswith(prefix):
        raise ValueError("召回健康检查仅支持 SQLite 数据库，请核对 DATABASE_URL")
    return Path(url[len(prefix) :])


def collect_recall_health(conn: sqlite3.Connection, days: int = 14) -> dict:
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        raise ValueError("days must be an int in [1, 365]")
    conn.row_factory = sqlite3.Row
    # ORM 落盘为无偏移 UTC、空格分隔文本；原生 SQL 参数必须保持同一口径。
    cutoff = (datetime.now(UTC) - timedelta(days=days)).replace(tzinfo=None)
    cutoff_text = cutoff.isoformat(sep=" ", timespec="microseconds")

    total = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ?", (cutoff_text,)
    ).fetchone()[0]
    zero = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ? AND zero_result = 1",
        (cutoff_text,),
    ).fetchone()[0]
    noise = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ? AND is_system_noise = 1",
        (cutoff_text,),
    ).fetchone()[0]
    last_row = conn.execute(
        "SELECT created_at FROM retrieval_event ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_time = last_row[0] if last_row else "无记录"

    daily = conn.execute(
        """SELECT substr(created_at, 1, 10) d, count(*) n, sum(zero_result) z
           FROM retrieval_event WHERE created_at >= ?
           GROUP BY d ORDER BY d DESC""",
        (cutoff_text,),
    ).fetchall()

    return {
        "window_days": days,
        "total": total,
        "zero_results": zero,
        "system_noise": noise,
        "last_retrieval": last_time,
        "daily": [(r[0], r[1], r[2]) for r in daily],
    }


def render_health(h: dict) -> str:
    zero_pct = f"{h['zero_results'] / h['total'] * 100:.1f}%" if h["total"] else "N/A"
    noise_pct = f"{h['system_noise'] / h['total'] * 100:.1f}%" if h["total"] else "N/A"
    lines = [
        "召回健康度",
        f"最近 {h['window_days']} 天：{h['total']} 次检索",
        f"零召回：{h['zero_results']}（{zero_pct}）",
        f"系统噪音：{h['system_noise']}（{noise_pct}）",
        f"最后检索：{h['last_retrieval']}",
        "",
        "每日检索趋势：",
    ]
    for d, n, _z in h["daily"]:
        bar = "█" * min(n, 40)
        lines.append(f"  {d}  {n:3d} 次  {bar}")
    if not h["total"]:
        lines.append("  ⚠ 无检索记录——Hermes 插件可能未加载或未在使用中")
    if h["zero_results"] > h["total"] * 0.5 and h["total"] > 10:
        lines.append(f"  ⚠ 零召回率 {zero_pct} > 50%——检查检索阈值或查询质量")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="召回健康检查（只读）")
    parser.add_argument("--days", type=int, default=14, help="统计窗口（1–365 天，默认 14）")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 365:
        parser.error("--days 必须是 1–365 之间的整数")

    try:
        path = _database_path()
    except ValueError as exc:
        sys.stderr.write(f"recall-health: {exc}\n")
        return 1
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            health = collect_recall_health(conn, args.days)
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.stderr.write(
            f"recall-health: 数据库读取失败（{type(exc).__name__}）：{path}；"
            "请检查文件、读取权限及 retrieval_event 表是否已初始化。\n"
        )
        return 1
    sys.stdout.write(render_health(health))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
