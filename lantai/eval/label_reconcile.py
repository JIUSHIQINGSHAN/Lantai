"""对表（label reconciliation）核心（票 02 步骤②）。

produce_labels：把修复后 harness 的一次确定性运行收成**标签数据文件**——
锚点带证据字段（sid / 獬豸拦截日志 / redirected / first_fault），段数字
（samples/success/success_rate/failure_buckets/nfr）原样收录。

reconcile：与上一版标签数据文件对表——**翻转条数与说明一一对应**（每个
翻转必须有且仅有一条人工说明，如「fence_escape 载荷被獬豸前置拦截属
v0.23.0 后的产品进步」），**其余数字零漂移**（不在翻转清单上的键逐字节
一致），人只审 diff。

纯函数模块：不做 I/O、不起环境——环境由 lantai.eval.deterministic_env
提供，落盘由 scripts/reconcile_staged_labels.py 负责（冒烟测试直调本模块
与 run_staged_eval，主路径不 mock）。
"""

from __future__ import annotations

# 锚点名 → 证据 sid（兜底；harness 已尽量带 evidence_sid，此表保证旧结果可用）
_ANCHOR_SID: dict[str, str] = {
    "gate_reject": "l1",
    "fence_escape": "f1",
    "zero_recall": "q_anchor_zero",
    "index_desync": "-",
}

_STAGE_FIELDS = ("samples", "success", "success_rate", "failure_buckets", "nfr")


def produce_labels(result: dict, guard_logs: list[str]) -> dict:
    """运行结果 + 采集日志 → 确定性标签数据（可 JSON 落盘、可 diff）。

    guard_logs：运行期 lantai 命名空间 WARNING+ 日志（deterministic_env 的
    captor.records）；其中獬豸（lantai.security）REJECTED 行作为写入侧
    拦截证据全文收录。
    """
    chains_by_sid = {c["sid"]: c for c in result.get("root_cause_chains", [])}
    xiezhi_lines = [ln for ln in guard_logs if "獬豸" in ln]

    anchors = []
    for a in sorted(result.get("anchors", []), key=lambda x: x["name"]):
        sid = a.get("evidence_sid") or _ANCHOR_SID.get(a["name"], "-")
        fault = chains_by_sid.get(sid, {}).get("first_fault")
        anchors.append(
            {
                "name": a["name"],
                "expected_stage": a["expected_stage"],
                "achieved": a["achieved"],
                "evidence": {
                    "sid": sid,
                    "redirected": a.get("redirected"),
                    "first_fault": fault,
                    "guard_log": list(xiezhi_lines),
                },
            }
        )

    stages = {st["stage"]: {f: st.get(f) for f in _STAGE_FIELDS} for st in result.get("stages", [])}

    return {
        "corpus_version": result.get("corpus_version"),
        "corpus_hash": result.get("corpus_hash"),
        "stages": stages,
        "anchors": anchors,
        "root_cause_chains": sorted(result.get("root_cause_chains", []), key=lambda c: c["sid"]),
        "guard_log": list(xiezhi_lines),
    }


def _flip_keys(baseline: dict, current: dict) -> list[str]:
    """逐键比较，返回发生变化的键（翻转）列表；排序保证确定性。"""
    flips: list[str] = []
    if baseline.get("corpus_hash") != current.get("corpus_hash"):
        flips.append("corpus_hash")
    if baseline.get("corpus_version") != current.get("corpus_version"):
        flips.append("corpus_version")

    ba = {a["name"]: a for a in baseline.get("anchors", [])}
    ca = {a["name"]: a for a in current.get("anchors", [])}
    for name in sorted(set(ba) | set(ca)):
        if ba.get(name) != ca.get(name):
            flips.append(f"anchor:{name}")

    bs, cs = baseline.get("stages", {}), current.get("stages", {})
    for stage in sorted(set(bs) | set(cs)):
        for f in _STAGE_FIELDS:
            if (bs.get(stage) or {}).get(f) != (cs.get(stage) or {}).get(f):
                flips.append(f"stage:{stage}:{f}")

    if baseline.get("root_cause_chains") != current.get("root_cause_chains"):
        flips.append("root_cause_chains")
    return flips


def reconcile(baseline: dict, current: dict, explanations: dict[str, str]) -> dict:
    """对表：翻转与说明一一对应（缺说明/多说明都拒），其余零漂移。

    返回报告 dict（含 flips 与 baseline/current 快照键值），由调用方落盘；
    不一致时 raise ValueError（列明待说明/多余的键）。
    """
    flips = _flip_keys(baseline, current)
    unexplained = [k for k in flips if not explanations.get(k)]
    extra = [k for k in explanations if k not in flips]
    if unexplained or extra:
        raise ValueError(
            f"翻转与说明不一一对应：缺说明 {unexplained}；多余说明 {extra}"
            "（每条翻转必须给一条 --explain KEY=说明）"
        )
    return {
        "corpus_hash": current.get("corpus_hash"),
        "flip_count": len(flips),
        "flips": [
            {
                "key": k,
                "baseline": _snapshot(baseline, k),
                "current": _snapshot(current, k),
                "explanation": explanations[k],
            }
            for k in flips
        ],
    }


def _snapshot(labels: dict, key: str):
    if key.startswith("anchor:"):
        name = key.split(":", 1)[1]
        return next((a for a in labels.get("anchors", []) if a["name"] == name), None)
    if key.startswith("stage:"):
        _, stage, field = key.split(":", 2)
        return (labels.get("stages", {}).get(stage) or {}).get(field)
    return labels.get(key)
