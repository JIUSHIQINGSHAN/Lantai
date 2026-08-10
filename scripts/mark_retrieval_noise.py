"""
存量检索事件噪音标记（任务 3）——幂等，可重复运行。

对 retrieval_event 里已落库的历史事件，按 is_system_noise 逻辑重新判定并回填标记，
避免系统噪音污染 dry-run 评估集。只 UPDATE 标记，绝不删除数据。

用法:
    python scripts/mark_retrieval_noise.py            # 默认生产库
    python scripts/mark_retrieval_noise.py --dry-run  # 只看不改
"""
import argparse
import sys

sys.path.insert(0, ".")  # 兼容任意 cwd 运行

from sqlmodel import select  # noqa: E402

from lantai.core.logger import logger  # noqa: E402
from lantai.models.tables import RetrievalEvent  # noqa: E402
from lantai.observability.retrieval_log import is_system_noise  # noqa: E402
from lantai.storage import db  # noqa: E402


def main(dry_run: bool = False) -> int:
    changed = 0
    noise_now = 0
    with db.get_session() as s:
        events = s.exec(select(RetrievalEvent)).all()
        for ev in events:
            judge = is_system_noise(ev.query_text)
            if judge:
                noise_now += 1
            if judge != ev.is_system_noise:
                changed += 1
                if not dry_run:
                    ev.is_system_noise = judge
                    s.add(ev)
        if not dry_run and changed:
            s.commit()
    total = len(events)
    logger.info(
        "noise mark: total=%d noise=%d changed=%d dry_run=%s",
        total, noise_now, changed, dry_run,
    )
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
