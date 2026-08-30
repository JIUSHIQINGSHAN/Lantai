# ADR-0027: 察窗——观察期窗口口径（连续 → 窗口内计数）

**日期**: 2026-08-28
**状态**: Accepted
**决策者**: 大哥
**来源**: 生产库实测：`reflect_run` 11 条记录中 scheduled 6 条，但 8/18–8/23
完全空档（服务未整夜运行），连续合格运行 1/7——按现行连环口径永不满足。

## 背景

CONTEXT.md 观察期定义：「只认 7 次连续、无异常且无 LLM 失败的 scheduled 记录」。
`scripts/reflect_observation_status.py::collect_observation_status` 实现为
`streak 从最近一天往前数，遇断日即停`。

但定时任务依赖服务整夜运行——8/18–8/23 五日空档导致连环永远断在 1，无法完成
观察期数据积累。实情是服务不稳定 ≠ 反思质量不合格——采样合格的 6 天中全部
`error=""`、`curate_failed=0`、`proposals_created=0`（curator 修复后 idle
是正常状态，不代表失败）。

## 决策

| 项 | 决策 |
|----|------|
| 口径 | 从「N 次连续」改为「滑动窗口内 N 次合格」——窗口大小 `REFLECT_OBSERVATION_WINDOW_DAYS`（默认 14），门槛 `REFLECT_OBSERVATION_REQUIRED_RUNS`（默认 7）。即：最近 14 天内至少有 7 天有合格 scheduled 运行 |
| 合格判定 | 不变：`source=scheduled`、`error` 空、`curate_failed=0`、`rejecter_failed=0`。同一日内多次运行中任一合格即算该日合格 |
| 默认值 | 窗口 14 天、门槛 7 次——与现行 7 天连续等宽（7 天线索），但容忍服务间断 |
| 命名 | 「察窗」——窗口观察口径，取自「察」= 审视、「窗」= 滑动时间窗口 |

## 理由

- 连续口径对服务稳定性要求远高于反思质量要求——6 天全是合格 idle 但连环断在
  日期空缺，这个门禁测的是 cron 可靠性而非反思质量；
- 窗口口径在语义上等价于「最近 14 天里有没有 7 天在跑且合格」——既能阻止
  长期不跑（cron 死亡），又不因短暂的放假/维护断链；
- 不降低门槛：14 天内 7 次 ≈ 每周至少跑 3.5 天，与现行 7 天连续的系统性
  要求一致。

## 影响

- `scripts/reflect_observation_status.py`：`collect_observation_status` 改为
  `windowed` 计数（反向遍历，累计合格天数直到窗口边界或满计数）。
- `lantai/core/settings.py`：`REFLECT_OBSERVATION_WINDOW_DAYS: int = 14`、
  `REFLECT_OBSERVATION_REQUIRED_RUNS: int = 7`
- `CONTEXT.md`：观察期定义更新（「连续」→「窗口中」）。
- 测试：`test_observation_status.py`（新建）——构造 14 天窗口数据的纯函数测试。
- 发布门禁 `release_check.py` 会调用 `--check`，口径变更后首次可能立即 PASS。

## 相关

- [ADR-0013](0013-naming-system.md) — 察窗命名登记
- [CONTEXT.md](../../CONTEXT.md) — 观察期词汇定义
- [v0.15 路线计划书](../plans/roadmap-2026-08-v015.md) — A 项观察期回填校准
