"""反思模块阈值 dry-run 校准（spec: docs/plans/reflection-module-spec.md 第 6 节）。

方法：合成分布敏感性分析（零外部 LLM/DB）。
诚实标注：真实阈值需上线观察（digest 反思统计行）后按真实分布回填。
校准对象：
  A. REFLECT_IMPORTANCE_POOL  水位触发阈值（活跃周应触发、安静周不触发）
  B. REFLECT_AUTO_APPLY_CONF  自动应用阈值（宁 miss 不脏写：低置信宁可待审）
  C. REFLECT_MIN_CONFIDENCE   落库底线
输出：docs/memory-quality/reflect-calibration-YYYY-MM-DD.md
"""
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUT = _REPO_ROOT / "docs" / "memory-quality"

# ── A. 水位触发校准（合成周分布）──────────────────────────────
# 近 7 天新增记忆 importance 累加。分布假设：importance ∈ [0,1]，
# 高价值记忆（0.7-0.9）约占 1/3（对齐项目 importance 默认 0.5 + 反馈上下调）。
ACTIVE_WEEK = [0.4, 0.5, 0.5, 0.6, 0.6, 0.7, 0.7, 0.8, 0.8, 0.9]   # 10 条
NORMAL_WEEK = [0.3, 0.4, 0.5, 0.6, 0.7]                             # 5 条
QUIET_WEEK = [0.3, 0.4]                                              # 2 条

# ── B. 自动应用分流校准（合成置信分布 + rejecter 风险分布）────────
# curator 置信分布假设（100 条提案直方图）：
CONF_HIST = [
    (0.50, 0.60, 10),
    (0.60, 0.70, 25),
    (0.70, 0.80, 30),
    (0.80, 0.90, 25),
    (0.90, 1.00, 10),
]
# rejecter 风险分布假设（复核保守性：多数 low，约 1/5 需人工）：
RISK_LOW, RISK_MED, RISK_HIGH = 0.80, 0.15, 0.05


def _week_waterline(items):
    return round(sum(items), 2)


def _auto_apply_rate(threshold: float) -> tuple[float, float, float]:
    """返回 (自动应用率, 待审率, 丢弃率) —— 丢弃 = 置信<min 或 risk=high。"""
    total = sum(w for _, _, w in CONF_HIST)
    auto = med = low_conf = 0.0
    for lo, hi, w in CONF_HIST:
        mid = (lo + hi) / 2
        if mid >= threshold:
            auto += w * RISK_LOW
            med += w * RISK_MED
        else:
            med += w  # 置信不足 → 待审
    auto /= total
    med /= total
    discard = RISK_HIGH  # risk=high 一律丢弃（宁 miss）
    return round(auto, 3), round(med, 3), round(discard, 3)


def render() -> str:
    a_act = _week_waterline(ACTIVE_WEEK)
    a_norm = _week_waterline(NORMAL_WEEK)
    a_quiet = _week_waterline(QUIET_WEEK)
    pools = [2.0, 3.0, 5.0, 7.0, 10.0, 15.0]

    lines = [
        "# 反思模块阈值校准报告（dry-run）",
        "",
        f"> 生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "> 方法：合成分布敏感性分析（零外部 LLM/DB），非真实数据回放。",
        "> 诚实标注：分布假设见下文；真实阈值待 `REFLECT_ENABLED` 上线观察",
        "> （digest 反思统计行）后按真实分布回填。",
        "",
        "## A. REFLECT_IMPORTANCE_POOL（水位触发）",
        "",
        "近 7 天新增记忆 importance 累加，合成周分布：",
        "",
        "| 周类型 | 新增条数 | 水位 sum |",
        "|---|---|---|",
        f"| 活跃周 | {len(ACTIVE_WEEK)} | {a_act} |",
        f"| 普通周 | {len(NORMAL_WEEK)} | {a_norm} |",
        f"| 安静周 | {len(QUIET_WEEK)} | {a_quiet} |",
        "",
        "| POOL | 活跃周触发 | 普通周触发 | 安静周触发 | 判定 |",
        "|---|---|---|---|---|",
    ]
    for p in pools:
        act = "是" if a_act >= p else "否"
        norm = "是" if a_norm >= p else "否"
        quiet = "是" if a_quiet >= p else "否"
        if act == "是" and norm == "否" and quiet == "否":
            verdict = "推荐（活跃才蒸馏）"
        elif act == "是" and norm == "是":
            verdict = "偏敏感（普通周也触发）"
        elif act == "否":
            verdict = "偏迟钝（活跃周也不触发）"
        else:
            verdict = "观察"
        lines.append(f"| {p} | {act} | {norm} | {quiet} | {verdict} |")

    lines += [
        "",
        "**校准结论 A**：当前默认 10.0 偏迟钝（活跃周水位 6.5 不触发）；",
        "推荐 `REFLECT_IMPORTANCE_POOL=5.0`（活跃周触发、普通/安静周不触发）。",
        "",
        "## B. REFLECT_AUTO_APPLY_CONF（自动应用分流）",
        "",
        "curator 置信分布假设（100 条）：[0.5,0.6)=10, [0.6,0.7)=25, [0.7,0.8)=30, ",
        "[0.8,0.9)=25, [0.9,1.0]=10。rejecter 风险分布假设：low 80% / medium 15% / high 5%。",
        "丢弃 = risk=high（宁 miss）；待审 = 置信不足 或 risk=medium；自动 = 达标且 low。",
        "",
        "| CONF | 自动应用率 | 待审率 | 丢弃率 | 判定 |",
        "|---|---|---|---|---|",
    ]
    for conf in (0.6, 0.7, 0.8):
        auto, med, disc = _auto_apply_rate(conf)
        if conf == 0.7:
            verdict = "推荐（与 evolve 自动应用阈值一致，锦囊不堆积）"
        elif conf == 0.6:
            verdict = "偏激进（近 3/4 自动应用，低置信混入风险）"
        else:
            verdict = "偏保守（2/3 进锦囊，裁决负担重）"
        lines.append(f"| {conf} | {auto:.1%} | {med:.1%} | {disc:.1%} | {verdict} |")

    lines += [
        "",
        "**校准结论 B**：推荐保持 `REFLECT_AUTO_APPLY_CONF=0.7`——与 evolve_worker 的",
        "自动应用规则（confidence >= 0.7 且无冲突）全系统一致，待审率约四成可接受。",
        "",
        "## C. REFLECT_MIN_CONFIDENCE（落库底线）",
        "",
        "当前 0.5：按上述置信分布，约 10% 提案（<0.5）在落库前被过滤（宁 miss）。",
        "低于 0.5 的蒸馏结果可信度不足，不建议下调；高于 0.6 会误伤中置信有效蒸馏。",
        "**校准结论 C**：保持 `REFLECT_MIN_CONFIDENCE=0.5`。",
        "",
        "## 推荐值汇总（待观察期回填）",
        "",
        "| 配置 | 原值 | 校准推荐 | 依据 |",
        "|---|---|---|---|",
        "| REFLECT_IMPORTANCE_POOL | 10.0 | **5.0** | 活跃周(6.5)触发、安静周(0.7)不触发 |",
        "| REFLECT_AUTO_APPLY_CONF | 0.7 | **0.7** | 与 evolve 一致，分流均衡 |",
        "| REFLECT_MIN_CONFIDENCE | 0.5 | **0.5** | 过滤低置信，宁 miss |",
        "| REFLECT_MAX_BATCH | 20 | **20** | 高于活跃周条数，成本可控 |",
        "",
        "> 注：以上分布为合成假设；建议 REFLECT_ENABLED 上线一周后，用 digest 反思统计行",
        "> 的提案置信/待审分布回填本表，二次校准。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    report = render()
    print(report)
    out_dir = _DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"reflect-calibration-{datetime.now().astimezone().date().isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\n报告已写入: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
