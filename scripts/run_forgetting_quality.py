"""遗忘质量自测 CLI：跑中文评测集 → 打印指标 + 落盘报告。

用法：
    python scripts/run_forgetting_quality.py              # 真实检索（LLM 意图 + embedding）
    python scripts/run_forgetting_quality.py --intent rule  # 跳过 LLM 意图分类（快速）
    python scripts/run_forgetting_quality.py --top-k 10
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from lantai.eval.chinese_memory_cases import build_chinese_dataset  # noqa: E402
from lantai.eval.forgetting_quality import evaluate_forgetting_quality  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUT = _REPO_ROOT / "docs" / "memory-quality"

_METRIC_LABELS = {
    "stale_hit_rate": "陈旧记忆残留率（越低越好）",
    "typo_recall_rate": "中文错别字容错命中率（越高越好）",
    "fresh_recall_rate": "对照组召回率（管道自检，应≈1）",
    "temporal_order_accuracy": "时效排序正确率（未生效过滤/过期降权）",
    "superseded_order_accuracy": "被取代记忆排序正确率（新值在前）",
    "superseded_residual_rate": "被取代记忆残留率（越低越好）",
}


def _make_search(intent_mode: str, top_k: int):
    from lantai.retrieval.hybrid import hybrid_search
    if intent_mode == "rule":
        from unittest.mock import patch
        from lantai.core.settings import settings as s
        patch("lantai.retrieval.hybrid.classify_intent",
              return_value={"intent": s.DEFAULT_INTENT,
                            "candidate_n": s.INTENT_CANDIDATE_SIZES.get(s.DEFAULT_INTENT, 10)}
              ).start()

    def search(query, **kw):
        kw.setdefault("top_k", top_k)
        kw.setdefault("use_rerank", False)
        return hybrid_search(query, **kw)
    return search


def render_report(result: dict) -> str:
    metrics = result["metrics"]
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"# 遗忘质量自测报告 — {result.get('dataset', '')}",
        "",
        f"> 生成时间：{now_str}",
        "",
        "## 指标",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
    ]
    for k, label in _METRIC_LABELS.items():
        lines.append(f"| {k} | {metrics.get(k, 0.0)} | {label} |")
    lines += ["", "## 逐条明细", ""]
    for q in result.get("per_query", []):
        lines.append(f"- [{q['category']}] `{q['query']}` → "
                     f"命中={bool(q['result_ids'])} ids={q['result_ids'][:5]}")
    lines.append("")
    lines.append("> 注：陈旧/残留类指标为诚实测量，可能暴露检索层真实缺口，"
                 "修复遵循「宁 miss 不脏写」由人工闸门裁决，不自动改写。")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台记忆 遗忘质量自测")
    ap.add_argument("--intent", choices=["llm", "rule"], default="llm",
                    help="意图分类方式：llm=真实API（慢），rule=规则fallback（快速评估）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--out", default=None, help="报告输出目录（默认 docs/memory-quality）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 指标")
    args = ap.parse_args()

    dataset = build_chinese_dataset()
    search = _make_search(args.intent, args.top_k)
    result = evaluate_forgetting_quality(dataset, search=search, top_k=args.top_k)

    if args.json:
        print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
        return 0

    report = render_report(result)
    print(report)
    out_dir = Path(args.out) if args.out else _DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now().astimezone().date().isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告已写入: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
