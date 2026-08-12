"""手动触发一次反思运行（观察期验证用，一次性脚本）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lantai.workers.reflect_worker import run_reflect_once


def main() -> None:
    t0 = time.time()
    res = run_reflect_once()
    print("elapsed_sec =", round(time.time() - t0, 1))
    for key in ("ok", "skipped", "waterline", "proposals_created",
                "auto_applied", "pending", "discarded"):
        print(key, "=", res.get(key))
    print("health_before =", res.get("health_before"))
    print("health_after =", res.get("health_after"))


if __name__ == "__main__":
    main()
