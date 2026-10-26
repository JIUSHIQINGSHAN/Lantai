"""对表脚本主路径锁定（票 02 步骤②，测试纪律：不 mock 冒烟）。

真实执行：真 SQLite（StaticPool 内存库）+ 真 FTS5 + 真内嵌 ChromaDB，
真实直调 run_staged_eval 全六段 → produce_labels → reconcile（对表脚本
scripts/reconcile_staged_labels.py 的核心路径同源调用这两个函数，本测试
即为该脚本主路径的冒烟锁定）。替身仅外部 embedding 面（deterministic_env，
纪律允许面）。

锁定三点：
① 主路径不炸且标签数据齐备：四预埋锚点各带证据字段（sid/獬豸日志位），
   段数字（samples/success/success_rate/buckets/nfr）原样收录；
② 零漂移：同一环境复跑 → produce → reconcile 基线自身 → flip_count==0；
③ 翻转与说明一一对应：人工翻转 fence_escape 的 achieved 后——
   无说明 → ValueError（列明缺说明键）；给说明 → 翻转记录带说明落档；
   多余说明 → 同样 ValueError。
"""

import pytest

from lantai.eval.deterministic_env import staged_deterministic_env
from lantai.eval.label_reconcile import produce_labels, reconcile
from lantai.eval.staged import run_staged_eval


def _run_once() -> dict:
    with staged_deterministic_env() as captor:
        result = run_staged_eval()
    return produce_labels(result, captor.records)


def test_produce_labels_main_path_smoke():
    labels = _run_once()

    anchors = {a["name"]: a for a in labels["anchors"]}
    assert set(anchors) == {
        "gate_reject",
        "index_desync",
        "zero_recall",
        "fence_escape",
    }, f"四预埋锚点必须齐备，实际：{sorted(anchors)}"
    for name, a in anchors.items():
        assert a["achieved"] is True, f"确定性环境下锚点 {name} 应 achieved"
        ev = a["evidence"]
        assert ev["sid"], f"{name} 证据字段缺 sid"
        assert isinstance(ev["guard_log"], list), f"{name} 证据字段缺獬豸日志位"

    # 段数字原样收录（与 run_staged_eval 结果一致）
    assert labels["stages"]["extract"]["nfr"] == 1.0  # 4 噪音全零产出
    assert labels["stages"]["inject"]["samples"] == 1  # 仅 f1 进注入段
    assert labels["corpus_hash"]

    # 证据 sid 落位正确
    assert anchors["gate_reject"]["evidence"]["sid"] == "l1"
    assert anchors["fence_escape"]["evidence"]["sid"] == "f1"
    assert anchors["zero_recall"]["evidence"]["sid"] == "q_anchor_zero"


def test_reconcile_zero_drift_on_rerun():
    baseline = _run_once()
    current = _run_once()
    report = reconcile(baseline, current, explanations={})
    assert report["flip_count"] == 0, f"同一确定性环境复跑必须零漂移：{report['flips']}"


def test_reconcile_flip_explanations_one_to_one():
    baseline = _run_once()
    current = _run_once()
    fence = next(a for a in current["anchors"] if a["name"] == "fence_escape")
    fence["achieved"] = False  # 模拟产品进步翻转（如獬豸前置拦截）
    fence["evidence"]["redirected"] = "gate"

    # 无说明 → 拒
    with pytest.raises(ValueError, match="anchor:fence_escape"):
        reconcile(baseline, current, explanations={})

    # 说明一一对应 → 翻转带说明落档，其余零漂移
    why = "fence_escape 载荷被獬豸前置拦截（v0.23.0 后产品进步，预期内）"
    report = reconcile(baseline, current, explanations={"anchor:fence_escape": why})
    assert report["flip_count"] == 1
    flip = report["flips"][0]
    assert flip["key"] == "anchor:fence_escape"
    assert flip["explanation"] == why
    assert flip["baseline"]["achieved"] is True
    assert flip["current"]["achieved"] is False

    # 多余说明 → 同样拒（一一对应是双向的）
    with pytest.raises(ValueError, match="多余说明"):
        reconcile(baseline, _run_once(), explanations={"anchor:fence_escape": why})
