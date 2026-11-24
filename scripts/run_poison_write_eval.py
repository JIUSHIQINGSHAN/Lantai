"""守藏写入投毒评测 CLI：跑载荷集 → 打印指标 + 落盘基线（ADR-0096）。

用法：
    python scripts/run_poison_write_eval.py              # 隔离临时库跑一次（不进生产库）
    python scripts/run_poison_write_eval.py --check      # 门禁：断言门槛（FAIL 退出码 1）
    python scripts/run_poison_write_eval.py --write-baseline  # 覆写基线 JSON（人工核对后提交）

隔离纪律（同 `scripts/run_forgetting_quality.py --check`）：默认**不碰**
生产 `LANTAI_HOME` / `DATABASE_URL`——自建临时目录 + 内存库，跑完即弃。
"""

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lantai.eval.poison_write import (  # noqa: E402
    GATES,
    build_poison_dataset,
    evaluate_poison_write,
    write_baseline,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BASELINE = _REPO_ROOT / "lantai" / "eval" / "poison_write_baseline.json"

_METRIC_LABELS = {
    "poison_count": "载荷条数",
    "clean_count": "干净对照条数",
    "guard_block_rate": "獬豸拦截率（载荷中被注入闸拦下的比例）",
    "external_ingest_rate": "外部来源直入率（越低越好，守藏主指标）",
    "dialogue_ingest_rate": "对话来源直入率（如实报告，守藏不管）",
    "external_hold_rate": "外部来源转锦囊率（越高越好）",
    "recall_rate": "已入库载荷召回率（分母=已入库）",
    "inject_rate": "注入帧带出率（分母=已入库，越低越好）",
    "clean_false_reject_rate": "干净对照误拒率（越低越好，必须为 0）",
}


def _isolated_env():
    """临时数据目录 + 独立 engine（跑完即弃，绝不落生产库）。

    `lantai.storage.db.engine` 在 import 时按旧 `DATABASE_URL` 建好——只改
    settings 不换 engine，跑的还是生产库（staged_deterministic_env 同一个坑）。
    这里显式重建 engine 并在收尾换回。
    """
    tmp = Path(tempfile.mkdtemp(prefix="lantai-poison-eval-"))
    from sqlalchemy import create_engine

    from lantai.core.settings import settings
    from lantai.storage import db as db_module

    saved = {
        "LANTAI_HOME": settings.LANTAI_HOME,
        "DATABASE_URL": settings.DATABASE_URL,
        "CHROMADB_PATH": settings.CHROMADB_PATH,
        "engine": db_module.engine,
    }
    settings.LANTAI_HOME = str(tmp)
    settings.DATABASE_URL = f"sqlite:///{tmp / 'eval.db'}"
    settings.CHROMADB_PATH = str(tmp / "chroma")
    db_module.engine = create_engine(
        settings.DATABASE_URL, echo=False, connect_args={"timeout": 30}
    )
    return tmp, saved


def _restore(saved: dict) -> None:
    from lantai.core.settings import settings
    from lantai.storage import db as db_module

    for k, v in saved.items():
        if k == "engine":
            db_module.engine = v
        else:
            setattr(settings, k, v)


def render_report(result: dict) -> str:
    metrics = result.get("metrics", {})
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"# 守藏写入投毒评测 — {result.get('dataset', '')}",
        "",
        f"> 生成时间：{now_str}",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
    ]
    for k, label in _METRIC_LABELS.items():
        lines.append(f"| {k} | {metrics.get(k, 0.0)} | {label} |")
    lines += ["", "## 门槛", "", "| 门槛 | 上限 |", "|---|---|"]
    for k, v in GATES.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 逐条明细", ""]
    for c in result.get("per_case", []):
        mark = "拦截" if c["guard_blocked"] else ("入库" if c["ingested"] else "转锦囊")
        lines.append(
            f"- [{c['kind']}/{c['interface']}/{c['source_type']}] {c['id']} → {mark}"
            f"（decision={c['decision'] or '-'}）"
        )
    lines.append("")
    return "\n".join(lines)


def _check(metrics: dict) -> tuple[bool, list[str]]:
    fails = []
    for gate, value in GATES.items():
        key = gate.removesuffix("_max")
        if key not in metrics:
            continue
        if metrics[key] > value:
            fails.append(f"{key}={metrics[key]} > 上限 {value}")
    # 覆盖率自检：门禁全 0 很容易「因为没测到」而假绿，这里锁分母
    if metrics.get("poison_count", 0) < 5:
        fails.append(f"poison_count={metrics.get('poison_count')} < 5（载荷不足以测）")
    if metrics.get("clean_count", 0) < 4:
        fails.append(f"clean_count={metrics.get('clean_count')} < 4（误拒测不出）")
    return (not fails, fails)


def main() -> int:
    ap = argparse.ArgumentParser(description="兰台 守藏写入投毒评测")
    ap.add_argument("--check", action="store_true", help="门禁模式：断言门槛（FAIL 退出码 1）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON 指标")
    ap.add_argument("--write-baseline", action="store_true", help="覆写基线 JSON")
    ap.add_argument("--out", default=None, help="报告输出目录（默认 docs/memory-quality）")
    args = ap.parse_args()

    tmp, saved = _isolated_env()
    try:
        from lantai.storage import db

        db.init_db()
        result = evaluate_poison_write(build_poison_dataset())
    finally:
        _restore(saved)

    metrics = result["metrics"]
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
    else:
        print(render_report(result))

    if args.write_baseline:
        write_baseline(result, _DEFAULT_BASELINE)
        print(f"\n基线已写入 {_DEFAULT_BASELINE}")

    ok = True
    if args.check:
        ok, fails = _check(metrics)
        if fails:
            print("\n门禁 FAIL：", file=sys.stderr)
            for f in fails:
                print(f"  - {f}", file=sys.stderr)
        else:
            print("\n门禁 PASS")

    print(f"\n临时数据目录（已弃用不清理，交由系统回收）：{tmp}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
