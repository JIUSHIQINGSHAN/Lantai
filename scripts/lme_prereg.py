"""LongMemEval 预注册清单生成（票 02 步骤④）：种子+题目 id 清单先落文件。

用法：PYTHONPATH=. python scripts/lme_prereg.py --n 50
缩题只减不换：后续 lme_run.py 取清单前缀即可（preregistration.json 不回改
增补/替换；缩题时另记 decision 字段）。
"""

import argparse
import json
import sys
from pathlib import Path

from lantai.eval.longmemeval import (
    assert_prereg_subset,
    load_dataset,
    preregistered_ids,
)

SEED = 20261026
DATASET = Path("data/longmemeval/longmemeval_s_cleaned.json")
OUT = Path("docs/benchmarks/longmemeval-2026-10/preregistration.json")
REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    args = ap.parse_args(argv)

    data = load_dataset(DATASET)
    ids = preregistered_ids(data, seed=SEED, n=args.n)
    assert_prereg_subset(ids, data)
    from collections import Counter

    type_of = {q["question_id"]: q["question_type"] for q in data}
    payload = {
        "seed": SEED,
        "dataset_revision": REVISION,
        "dataset_file": "data/longmemeval/longmemeval_s_cleaned.json",
        "n": args.n,
        "question_ids": ids,
        "type_distribution": dict(Counter(type_of[i] for i in ids)),
        "metric_prereg": {
            "primary": "distinct-session recall@2 / recall@5（会话级）",
            "gate_skip_policy": "gate 跳过=miss 计入全题分母，条件召回附报",
            "ablation_configs": {
                "vector-only": {
                    "RETRIEVAL_W_VECTOR": 1.0,
                    "RETRIEVAL_W_BM25": 0.0,
                    "RETRIEVAL_W_FTS": 0.0,
                },
                "bm25-only": {
                    "RETRIEVAL_W_VECTOR": 0.0,
                    "RETRIEVAL_W_BM25": 1.0,
                    "RETRIEVAL_W_FTS": 0.0,
                },
                "hybrid-default": {
                    "RETRIEVAL_W_VECTOR": 0.6,
                    "RETRIEVAL_W_BM25": 0.25,
                    "RETRIEVAL_W_FTS": 0.05,
                },
            },
            "common_env": {"VERBATIM_IN_RECALL": "true", "RERANKER_ENABLED": "false"},
            "shrink_rule": "缩题只减不换（取清单前缀），清单不回改",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"预注册落盘: {OUT}（seed={SEED}, n={args.n}）")
    print("题型分布:", payload["type_distribution"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
