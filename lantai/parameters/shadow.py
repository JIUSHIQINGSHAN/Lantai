"""Step 7 影子观察期 — 决策逻辑纯函数。

设计原则：
- **保守优先**：宁可 hold 也不误 promote（人工闸门兜底，误判成本远高于延迟成本）
- 纯函数，零 DB 依赖，输入 compute_metrics 输出即可测
- 规则可审计：reason 字符串明确说明触发哪条护栏

接口契约见 docs/step7-shadow-task-split.md。
"""
from typing import Optional


def evaluate_window(base: dict, shadow: dict, *,
                    zero_result_delta: float = 0.05,
                    avg_result_delta: float = 1.0,
                    jaccard_floor: float = 0.7) -> dict:
    """判定影子观察窗结果。

    base/shadow: compute_metrics 输出（含 zero_result_rate / avg_result_count /
    jaccard_vs_baseline）。

    护栏规则（任一触发即 rollback）：
      1. 漏检恶化：shadow.zero_result_rate - base.zero_result_rate > zero_result_delta
      2. 召回骤降：shadow.avg_result_count < base.avg_result_count - avg_result_delta
      3. 召回偏离：jaccard_vs_baseline < jaccard_floor（有基线时）

    全输入空/缺样本 → hold（数据不足不判定，交人工）。
    """
    # ── 数据不足防御 ──
    base_samples = base.get("sample_count") or 0
    shadow_samples = shadow.get("sample_count") or 0
    if base_samples == 0 and shadow_samples == 0:
        return {"verdict": "hold", "reason": "both_empty", "signals": {}}

    # ── 指标提取（缺省按最坏情况防御） ──
    base_zero = base.get("zero_result_rate")
    shadow_zero = shadow.get("zero_result_rate")
    base_avg = base.get("avg_result_count")
    shadow_avg = shadow.get("avg_result_count")
    jaccard = shadow.get("jaccard_vs_baseline")

    signals = {
        "base_zero_result_rate": base_zero,
        "shadow_zero_result_rate": shadow_zero,
        "base_avg_result_count": base_avg,
        "shadow_avg_result_count": shadow_avg,
        "jaccard_vs_baseline": jaccard,
    }

    # ── 护栏 1：漏检恶化（只比都有效的） ──
    if base_zero is not None and shadow_zero is not None:
        if shadow_zero - base_zero > zero_result_delta:
            return {
                "verdict": "rollback",
                "reason": f"zero_result 恶化: {shadow_zero:.4f} - {base_zero:.4f} > {zero_result_delta}",
                "signals": signals,
            }

    # ── 护栏 2：召回骤降（只比都有效的） ──
    if base_avg is not None and shadow_avg is not None:
        if shadow_avg < base_avg - avg_result_delta:
            return {
                "verdict": "rollback",
                "reason": f"avg_result 骤降: {shadow_avg:.2f} < {base_avg:.2f} - {avg_result_delta}",
                "signals": signals,
            }

    # ── 护栏 3：召回偏离（有基线时才检查） ──
    if jaccard is not None:
        if jaccard < jaccard_floor:
            return {
                "verdict": "rollback",
                "reason": f"jaccard 偏离: {jaccard:.4f} < {jaccard_floor}",
                "signals": signals,
            }

    # ── 通过全部护栏 → promote（但保守：任一关键指标缺失时 hold） ──
    if base_zero is None or shadow_zero is None or base_avg is None or shadow_avg is None:
        return {
            "verdict": "hold",
            "reason": "key_metrics_missing",
            "signals": signals,
        }

    return {
        "verdict": "promote",
        "reason": "all_guardrails_passed",
        "signals": signals,
    }


def shadow_is_due(window) -> bool:
    """观察期是否到期（check_deadline 已过）。

    只依赖 window.check_deadline；无 deadline 视为未到期。
    """
    from lantai.core.time import utcnow
    deadline = getattr(window, "check_deadline", None)
    if deadline is None:
        return False
    now = utcnow()
    if deadline.tzinfo is None and now.tzinfo is not None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    return now >= deadline


def decide_promote_target(window, *, min_promote_days: int = 0) -> bool:
    """promote 前置检查：状态必须 observing 且到期。

    min_promote_days: 最短观察天数（防过早 promote）。
    """
    status = getattr(window, "status", None)
    if status != "observing":
        return False
    if not shadow_is_due(window):
        return False
    if min_promote_days > 0:
        from lantai.core.time import utcnow
        from datetime import timedelta
        started = getattr(window, "started_at", None)
        if started is None:
            return False
        now = utcnow()
        if started.tzinfo is None and now.tzinfo is not None:
            started = started.replace(tzinfo=now.tzinfo)
        if now - started < timedelta(days=min_promote_days):
            return False
    return True
