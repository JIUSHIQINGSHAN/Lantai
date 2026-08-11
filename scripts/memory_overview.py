"""记忆概览 CLI：一眼看清记忆系统现状（只读，不写库）。

用法：
    python scripts/memory_overview.py            # Markdown 概览
    python scripts/memory_overview.py --json     # JSON 输出（脚本友好）
"""
import argparse
import json
import sys

sys.path.insert(0, ".")

from lantai.ops.overview import get_overview  # noqa: E402


def _fmt_markdown(o: dict) -> str:
    m = o["memories"]
    lane = ", ".join(f"{k}={v}" for k, v in sorted(m["by_lane"].items())) or "（无）"
    cls = ", ".join(f"{k}={v}" for k, v in sorted(m["by_decay_class"].items())) or "（无）"
    return "\n".join([
        "# 记忆概览",
        "",
        f"- 生成时间：{o['generated_at']}",
        f"- 记忆总数：{m['total']}（active {m['active']} / archived {m['archived']}）",
        f"- 分轨 lane：{lane}",
        f"- 衰减类：{cls}",
        f"- 待审候选：{o['candidates_pending_review']}",
        f"- 检查点版本：{o['checkpoints']}",
        f"- 待审提案：{o['proposals_pending']}",
    ]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆概览（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    args = ap.parse_args()
    overview = get_overview()
    if args.json:
        print(json.dumps(overview, ensure_ascii=False, indent=2))
    else:
        print(_fmt_markdown(overview))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
