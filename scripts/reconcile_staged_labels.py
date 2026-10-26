"""staged 标签对表 CLI（票 02 步骤②）：修复后 harness 跑预埋锚点，
产出带证据字段（獬豸拦截日志/sid）的标签数据文件；与上一版基线对表。

用法：
    PYTHONPATH=. python scripts/reconcile_staged_labels.py \
        [--baseline docs/benchmarks/staged-labels-2026-10.json] \
        [--out docs/benchmarks/staged-labels-2026-10.json] \
        [--explain anchor:fence_escape="獬豸前置拦截（v0.23.0 后产品进步，预期内）"]

纪律：确定性环境（真 SQLite/FTS5/Chroma + 外部 embed 面替身，同
tests/test_staged_anchor_miss.py 范式）；输出文件不含时间戳/run_id——
同一环境复跑逐字节一致，人只审 diff；翻转必须带一一对应的人工说明，
缺说明即退出码 1（宁 miss 不脏写）。
"""

import argparse
import json
import sys
from pathlib import Path

from lantai.eval.deterministic_env import staged_deterministic_env
from lantai.eval.label_reconcile import produce_labels, reconcile
from lantai.eval.staged import run_staged_eval

DEFAULT_OUT = Path("docs/benchmarks/staged-labels-2026-10.json")


def parse_explanations(raw: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw:
        key, sep, val = item.partition("=")
        if not sep or not key or not val:
            raise SystemExit(f"--explain 格式应为 KEY=说明，收到：{item!r}")
        out[key] = val
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--baseline", type=Path, default=None, help="上一版标签数据文件（缺省=产出首版基线）"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--explain", action="append", default=[], metavar="KEY=说明", help="翻转说明，可多次"
    )
    args = ap.parse_args(argv)

    explanations = parse_explanations(args.explain)

    with staged_deterministic_env() as captor:
        result = run_staged_eval()
    labels = produce_labels(result, captor.records)

    if args.baseline is not None and args.baseline.exists():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        base_labels = baseline.get("labels", baseline)  # 兼容含 flips 报告壳的旧文件
        report = reconcile(base_labels, labels, explanations)
    else:
        if explanations:
            raise SystemExit("无基线（首版）不应携带 --explain 说明")
        report = {"corpus_hash": labels["corpus_hash"], "flip_count": 0, "flips": []}

    payload = {"labels": labels, "reconciliation": report}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"标签数据文件: {args.out}")
    print(f"corpus_hash: {labels['corpus_hash']} | 翻转 {report['flip_count']} 条")
    for f in report["flips"]:
        print(
            f"  [翻转] {f['key']}: {f['baseline']!r} -> {f['current']!r} | 说明: {f['explanation']}"
        )
    achieved = {a["name"]: a["achieved"] for a in labels["anchors"]}
    print(f"锚点: {achieved}")
    return 0 if report["flip_count"] == len(report["flips"]) else 1


if __name__ == "__main__":
    sys.exit(main())
