"""
lantai/cognition/blb.py
Behavioral Learning Benchmark 计算器（v0.4）

提供 4 个量化指标：
- learning_rate: 成功避免历史错误的任务数 / 可利用历史经验的任务总数
- error_recurrence_rate: 同类错误再次出现次数 / 遇到同类任务次数
- rule_adoption_rate: Rule 出现在认知上下文中次数 / Rule 被创建次数
- regression_rate: 已学会行为后来退化的比例

全部纯函数，无 LLM 调用，供测试和 /cognitive/blb-report 端点使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BLBReport:
    """BLB 4 项指标报告。"""

    learning_rate: float = 0.0  # [0, 1]，越高越好
    error_recurrence_rate: float = 0.0  # [0, 1]，越低越好
    rule_adoption_rate: float = 0.0  # [0, 1]，越高越好
    regression_rate: float = 0.0  # [0, 1]，越低越好
    details: dict = field(default_factory=dict)
    summary: str = ""


def compute_learning_rate(
    similar_tasks_count: int,
    tasks_avoiding_error: int,
) -> float:
    """
    Learning Rate = 成功避免历史错误的任务数 / 可利用历史经验的任务总数。

    Args:
        similar_tasks_count: 遇到与历史失败相似任务的总次数（分母）
        tasks_avoiding_error: 其中成功避免历史错误的次数（分子）

    Returns:
        [0.0, 1.0]，higher is better
    """
    if similar_tasks_count == 0:
        return 0.0
    return round(min(1.0, tasks_avoiding_error / similar_tasks_count), 4)


def compute_error_recurrence_rate(
    similar_task_encounters: int,
    error_recurrences: int,
) -> float:
    """
    Error Recurrence Rate = 同类错误再次出现次数 / 遇到同类任务次数。

    Args:
        similar_task_encounters: 遇到与历史失败相似任务的总次数（分母）
        error_recurrences: 其中同类错误再次发生的次数（分子）

    Returns:
        [0.0, 1.0]，lower is better
    """
    if similar_task_encounters == 0:
        return 0.0
    return round(min(1.0, error_recurrences / similar_task_encounters), 4)


def compute_rule_adoption_rate(
    rules_injected: int,
    rules_in_context: int,
) -> float:
    """
    Rule Adoption Rate = Rule 实际出现在认知上下文中次数 / Rule 被注入次数。

    说明：这里用"注入"（rules_injected）= Rule 被创建且有相关 task 出现的次数；
    "在 context 中"= 相关 task 的 cognitive_context 包含该 Rule 的次数。

    Args:
        rules_injected: Rule 创建后遇到相关任务的次数（分母）
        rules_in_context: 其中 Rule 出现在 context 中的次数（分子）

    Returns:
        [0.0, 1.0]，higher is better
    """
    if rules_injected == 0:
        return 0.0
    return round(min(1.0, rules_in_context / rules_injected), 4)


def compute_regression_rate(
    learned_behaviors: int,
    regressed_behaviors: int,
) -> float:
    """
    Regression Rate = 已学会行为后来退化的比例。

    Args:
        learned_behaviors: 已确认学会的行为数（分母）
        regressed_behaviors: 其中后来退化（重新出现错误）的行为数（分子）

    Returns:
        [0.0, 1.0]，lower is better (0 = 无退化)
    """
    if learned_behaviors == 0:
        return 0.0
    return round(min(1.0, regressed_behaviors / learned_behaviors), 4)


def compute_blb_report(
    similar_tasks: int,
    tasks_avoiding: int,
    error_recurrences: int,
    rules_injected: int,
    rules_in_context: int,
    learned_behaviors: int = 0,
    regressed_behaviors: int = 0,
) -> BLBReport:
    """
    一次性计算全部 4 项 BLB 指标。

    Returns:
        BLBReport with all 4 metrics filled.
    """
    lr = compute_learning_rate(similar_tasks, tasks_avoiding)
    err = compute_error_recurrence_rate(similar_tasks, error_recurrences)
    adoption = compute_rule_adoption_rate(rules_injected, rules_in_context)
    regression = compute_regression_rate(learned_behaviors, regressed_behaviors)

    summary_parts = [
        f"Learning Rate: {lr:.1%}",
        f"Error Recurrence: {err:.1%}",
        f"Rule Adoption: {adoption:.1%}",
        f"Regression Rate: {regression:.1%}",
    ]

    return BLBReport(
        learning_rate=lr,
        error_recurrence_rate=err,
        rule_adoption_rate=adoption,
        regression_rate=regression,
        details={
            "similar_tasks": similar_tasks,
            "tasks_avoiding": tasks_avoiding,
            "error_recurrences": error_recurrences,
            "rules_injected": rules_injected,
            "rules_in_context": rules_in_context,
            "learned_behaviors": learned_behaviors,
            "regressed_behaviors": regressed_behaviors,
        },
        summary=" | ".join(summary_parts),
    )
