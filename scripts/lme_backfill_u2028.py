"""v0.25 票 01 补齐脚本：3 题 U+2028 缺行 + caf03d32 1 缺行，经 /import/jsonl
API 单通道重灌（复用 lme_run 的双闸 + 题级库 + checkpoint 纪律）。

做法：对 4 题各起题级评测服务（同 _server_env/_assert_double_gate），整题
corrected payload（ensure_ascii=True）幂等重导：已落库行记 duplicate，
缺行记 imported。报告落 docs/benchmarks/longmemeval-2026-10/backfill_report.json。
"""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "scripts")
sys.path.insert(0, ".")

import lme_run  # noqa: E402
import requests  # noqa: E402

from lantai.eval.longmemeval import (  # noqa: E402
    half_question_detect,
    import_lines_for_question,
    load_dataset,
)

OUT = lme_run.OUT
PORT = lme_run.PORT
BASE = lme_run.BASE

TARGETS = ["caf03d32", "25e5aa4f", "fea54f57", "0ddfec37_abs"]

data = load_dataset(lme_run.DATA)
by = {q["question_id"]: q for q in data}

report = []
for qid in TARGETS:
    q = by[qid]
    lines = import_lines_for_question(q)
    payload = "\n".join(json.dumps(l, ensure_ascii=True) for l in lines)
    proc = lme_run._start_server(None, qid)  # 双闸在 _start_server 内强制
    try:
        t0 = time.time()
        r = requests.post(
            f"{BASE}/import/jsonl",
            json={"text": payload},
            headers=lme_run._api_headers(),
            timeout=1800,
        )
        r.raise_for_status()
        rep = r.json()
        done = rep.get("imported", 0) + rep.get("duplicates", 0) + len(rep.get("invalid", []))
        # 探针轮：再导一次，全 duplicate 才算齐
        r2 = requests.post(
            f"{BASE}/import/jsonl",
            json={"text": payload},
            headers=lme_run._api_headers(),
            timeout=1800,
        )
        r2.raise_for_status()
        rep2 = r2.json()
        rec = {
            "question_id": qid,
            "expected": len(lines),
            "first_pass": {
                "imported": rep.get("imported"),
                "duplicates": rep.get("duplicates"),
                "invalid_n": len(rep.get("invalid", [])),
                "errors": rep.get("errors", [])[:3],
            },
            "probe_pass": {
                "imported": rep2.get("imported"),
                "duplicates": rep2.get("duplicates"),
                "invalid_n": len(rep2.get("invalid", [])),
            },
            "done_after_first": done,
            "half_question": half_question_detect(len(lines), done),
            "backfill_complete": rep2.get("duplicates") == len(lines)
            and rep2.get("imported") == 0
            and not rep2.get("invalid"),
            "latency_s": round(time.time() - t0, 1),
        }
    finally:
        proc.kill()
    report.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)

out_path = OUT / "backfill_report.json"
out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("written", out_path)
all_ok = all(r["backfill_complete"] and not r["half_question"] for r in report)
print("BACKFILL", "OK" if all_ok else "INCOMPLETE")
sys.exit(0 if all_ok else 2)
