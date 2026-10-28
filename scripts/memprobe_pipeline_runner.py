"""MemProbe 票 03 全自动接力流水线驱动脚本。
依次推进：
1. 等待 naive_rag 跑完全量 56 题；
2. 自动启动 lantai_verbatim_full 跑完全量 56 题（带进程健康检查与段级落盘）；
3. 自动运行 memprobe_summarize.py 生成 summary.json / verify.json。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path("D:/Projects/记忆")
EVID_DIR = REPO_ROOT / "docs/benchmarks/memprobe-2026-10"
REPORT_PATH = EVID_DIR / "report.json"
FORMAL_SH = REPO_ROOT / "scripts/memprobe_run_formal.sh"


def get_counts():
    if not REPORT_PATH.exists():
        return 0, 0, 0
    try:
        d = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        nr = len(d.get("naive_rag", {}))
        fc = len(d.get("full_context", {}))
        lv = len(d.get("lantai_verbatim_full", {}))
        return nr, fc, lv
    except Exception:
        return 0, 0, 0


def main():
    print(f"[{time.strftime('%H:%M:%S')}] MemProbe 自动流水线启动...", flush=True)

    # 1. 监控 naive_rag
    while True:
        nr, fc, lv = get_counts()
        print(
            f"[{time.strftime('%H:%M:%S')}] 当前进度: naive_rag={nr}/56, fc={fc}/56, lantai={lv}/56",
            flush=True,
        )
        if nr >= 56:
            print(f"[{time.strftime('%H:%M:%S')}] naive_rag 已达 56/56！", flush=True)
            break
        time.sleep(15)

    # 2. 推进 lantai_verbatim_full
    while True:
        nr, fc, lv = get_counts()
        if lv >= 56:
            print(f"[{time.strftime('%H:%M:%S')}] lantai_verbatim_full 已达 56/56！", flush=True)
            break
        print(
            f"[{time.strftime('%H:%M:%S')}] 启动/续跑 lantai completion-pass (当前 {lv}/56)...",
            flush=True,
        )
        ret = subprocess.run(["bash", str(FORMAL_SH), "lantai", "all"], cwd=str(REPO_ROOT))
        print(
            f"[{time.strftime('%H:%M:%S')}] lantai run exited with code {ret.returncode}",
            flush=True,
        )
        time.sleep(5)

    # 3. 汇总与对账
    print(
        f"[{time.strftime('%H:%M:%S')}] 三键全部 56/56 完成，开始运行 memprobe_summarize.py...",
        flush=True,
    )
    ret = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/memprobe_summarize.py")], cwd=str(REPO_ROOT)
    )
    if ret.returncode == 0:
        print(
            f"[{time.strftime('%H:%M:%S')}] memprobe_summarize.py 运行成功，summary.json 和 verify.json 已生成！",
            flush=True,
        )
    else:
        print(
            f"[{time.strftime('%H:%M:%S')}] memprobe_summarize.py 退出码 {ret.returncode}",
            flush=True,
        )


if __name__ == "__main__":
    main()
