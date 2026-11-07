"""存量检索原话一次性抹除（v0.68 票 10 / ADR-0084 §2.6）。

背景：`mark_missed_receipts` 的抹文只覆盖「转移瞬间」的行；真实库里已存在的
pending/missed 行（插件崩溃窗、未挂载扫描前的积压）带原话躺在库里。本脚本做
一次性存量 scrub：`receipt_status in (pending, missed)` 的行抹
`query_text`/`query_norm_hash`，acked/vetoed 零接触（acked 是弱标注源、vetoed
已由 mark_vetoed 抹过）。

用法：
    python scripts/scrub_retrieval_query_text.py            # dry-run：只统计
    python scripts/scrub_retrieval_query_text.py --apply    # 写库

幂等：只统计/处理「仍有原文」的行，重复运行第二遍改动数为 0。
宁 miss 不脏写：不修正、不改写，只置空——原文不可恢复（这是目的）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402

from lantai.models.tables import RetrievalEvent  # noqa: E402
from lantai.storage.db import get_session  # noqa: E402

TARGET_STATUSES = ("pending", "missed")


def scrub(apply: bool = False) -> dict:
    """扫 pending/missed 行抹原话；返回各状态实改（dry-run 为将改）计数。"""
    counts: dict[str, int] = {}
    with get_session() as s:
        for status in TARGET_STATUSES:
            rows = s.exec(
                select(RetrievalEvent).where(
                    RetrievalEvent.receipt_status == status,
                    RetrievalEvent.query_text != "",
                )
            ).all()
            counts[status] = len(rows)
            if apply:
                for ev in rows:
                    ev.query_text = ""
                    ev.query_norm_hash = ""
                    s.add(ev)
        if apply:
            s.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="pending/missed 检索事件原话一次性抹除（票 10）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真写库；缺省 dry-run 只统计不改任何行",
    )
    args = parser.parse_args()

    counts = scrub(apply=args.apply)
    mode = "APPLY（已写库）" if args.apply else "DRY-RUN（未写库，加 --apply 执行）"
    total = sum(counts.values())
    for status, n in counts.items():
        print(f"{status}: {n}")
    print(
        f"合计 {total} 行{'已抹除 query_text/query_norm_hash' if args.apply else '待抹除'}——{mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
