"""召回健康检查（只读）。

检查最近检索事件：最后检索时间、零召回率、系统噪音比例、检索趋势。
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

from lantai.core.settings import settings  # noqa: E402

def _database_path() -> Path:
    prefix = "sqlite:///"
    url = settings.DATABASE_URL
    if not url.startswith(prefix):
        raise ValueError("recall health requires a SQLite database")
    return Path(url[len(prefix):])

def collect_recall_health(conn: sqlite3.Connection, days: int = 14) -> dict:
    conn.row_factory = sqlite3.Row
    now = datetime.now()
    cutoff = now - timedelta(days=days)

    total = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ?",
        (cutoff.isoformat(),)).fetchone()[0]
    zero = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ? AND zero_result = 1",
        (cutoff.isoformat(),)).fetchone()[0]
    noise = conn.execute(
        "SELECT count(*) FROM retrieval_event WHERE created_at >= ? AND is_system_noise = 1",
        (cutoff.isoformat(),)).fetchone()[0]
    last_row = conn.execute(
        "SELECT created_at FROM retrieval_event ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_time = last_row[0] if last_row else "无记录"

    daily = conn.execute(
        """SELECT substr(created_at, 1, 10) d, count(*) n, sum(zero_result) z
           FROM retrieval_event WHERE created_at >= ?
           GROUP BY d ORDER BY d DESC""",
        (cutoff.isoformat(),)).fetchall()

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
    for d, n, z in h["daily"]:
        bar = "█" * min(n, 40)
        lines.append(f"  {d}  {n:3d} 次  {bar}")
    if not h["total"]:
        lines.append("  ⚠ 无检索记录——Hermes 插件可能未加载或未在使用中")
    if h["zero_results"] > h["total"] * 0.5 and h["total"] > 10:
        lines.append(f"  ⚠ 零召回率 {zero_pct} > 50%——检查检索阈值或查询质量")
    return "\n".join(lines) + "\n"

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="召回健康检查（只读）")
    parser.add_argument("--days", type=int, default=14, help="统计窗口（天，默认 14）")
    args = parser.parse_args(argv)

    path = _database_path()
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        health = collect_recall_health(conn, args.days)
    sys.stdout.write(render_health(health))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
