"""v0.25 票 01 补齐脚本：经 /import/jsonl API 单通道重灌。

复用 lme_run 的双闸、题级库和 checkpoint 纪律；导入模块不执行灌库。
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from lantai.eval.longmemeval import (
    half_question_detect,
    import_lines_for_question,
    load_dataset,
)
from scripts import lme_run

OUT = lme_run.OUT
PORT = lme_run.PORT
BASE = lme_run.BASE
TARGETS = ["caf03d32", "25e5aa4f", "fea54f57", "0ddfec37_abs"]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="重灌隔离评测库缺失行").parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data = load_dataset(lme_run.DATA)
    by = {q["question_id"]: q for q in data}
    report = []
    for qid in TARGETS:
        q = by[qid]
        lines = import_lines_for_question(q)
        payload = "\n".join(json.dumps(line, ensure_ascii=True) for line in lines)
        proc = lme_run._start_server(None, qid)
        try:
            t0 = time.time()
            response = requests.post(
                f"{BASE}/import/jsonl",
                json={"text": payload},
                headers=lme_run._api_headers(),
                timeout=1800,
            )
            response.raise_for_status()
            first = response.json()
            done = (
                first.get("imported", 0)
                + first.get("duplicates", 0)
                + len(first.get("invalid", []))
            )
            probe_response = requests.post(
                f"{BASE}/import/jsonl",
                json={"text": payload},
                headers=lme_run._api_headers(),
                timeout=1800,
            )
            probe_response.raise_for_status()
            probe = probe_response.json()
            record = {
                "question_id": qid,
                "expected": len(lines),
                "first_pass": {
                    "imported": first.get("imported"),
                    "duplicates": first.get("duplicates"),
                    "invalid_n": len(first.get("invalid", [])),
                    "errors": first.get("errors", [])[:3],
                },
                "probe_pass": {
                    "imported": probe.get("imported"),
                    "duplicates": probe.get("duplicates"),
                    "invalid_n": len(probe.get("invalid", [])),
                },
                "done_after_first": done,
                "half_question": half_question_detect(len(lines), done),
                "backfill_complete": probe.get("duplicates") == len(lines)
                and probe.get("imported") == 0
                and not probe.get("invalid"),
                "latency_s": round(time.time() - t0, 1),
            }
        finally:
            lme_run._stop_owned_process(proc)
        report.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    out_path = OUT / "backfill_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", out_path)
    all_ok = all(row["backfill_complete"] and not row["half_question"] for row in report)
    print("BACKFILL", "OK" if all_ok else "INCOMPLETE")
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
