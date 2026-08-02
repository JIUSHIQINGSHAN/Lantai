# coalesce 冲刷参数校准

Type: prototype
Status: resolved
Blocked by: 02

## Question

aiduMEM 的冲刷参数：`idle=4s` / `window=12s` / `max_parts=8` / `max_chars=2000` / `max_single=500`。

这些参数是在 aiduMEM 的消息分布下调校的。remembrance 的消息分布可能不同（用户习惯、输入长度、对话节奏）。需要：

1. 采集本项目实际消息样本（或模拟），测量消息间隔分布、长度分布
2. 搭建一次性原型，用不同参数组合跑模拟，观察合并率和延迟
3. 确认参数区间或给出本项目专属参数

**原型即弃**：用 /prototype 技能做一次性原型，回答完即弃。

## Answer

设计决议（grilling 2026-08-02 与用户确认，原型实现延至 Phase 3-6）：

### 1. 参数校准方式

- 用 aiduMEM 的 50 问句作为模拟输入 + 人工编写的短对话序列
- 不需要真实用户数据

### 2. 输出指标

- 合并率（buffered vs sync 比例）
- 平均批大小
- P50/P95 flush 延迟
- 对照 aiduMEM 默认参数跑 baseline，再调参

### 3. lane 参数差异

- 初版全部用 aiduMEM 默认值（idle 4s / window 12s / max_parts 8 / max_chars 2000）
- 原型跑完后再按 lane 差异化
- `general` lane = 默认值，`chat` lane 可能更短（更快 flush），`fact` lane 可能更长（等更多上下文）
