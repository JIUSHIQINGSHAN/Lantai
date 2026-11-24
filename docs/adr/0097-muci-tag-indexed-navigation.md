# ADR-0097: 目次标签主题导航与全局路标

**日期**: 2026-11-25  
**状态**: Accepted  
**决策者**: 本轮实现沿用 ADR-0065 的既有目次授权  
**关联**: ADR-0065、ADR-0048、`.scratch/muci-navigation-v2/spec.md`

## 背景

ADR-0065 定义目次应包含 domain/lane 分级、主题/实体簇、摘要与下钻，以及最新事件时间和平均重要度。现有实现仅输出扁平 `sections`，标签没有进入导航层，且 `latest_event_at` 错把事务轴 `updated_at` 当作事件轴时间；Agent 仍需在多个板块间自行拼全局主题脉络。

## 决策

1. 保留既有 `sections` 形状，增量增加 `outline`（domain → lane → topic）与 `landmarks`，避免破坏 REST、MCP 和现有调用方。
2. 主题簇只以 ACL 可见、生命周期允许进入读面的 `MemoryItem.tags` 构建。规范化键采用首尾去空白与 `casefold()`；不做同义词归并、不调用 embedding/LLM，也不把 `MemoryScene`、`MemoryNode` 或 `tree_path` 并入，避免扩展授权面。
3. 新增可选精确 `topic` 筛选，和既有 domain/lane 条件求交；其匹配键与主题键采用相同规范化规则。topic 过滤发生在 ACL 剪枝之后。
4. `outline` 在 compact 模式下每条分轨显示前 5 个主题并报告省略数；full 模式显示全部。`landmarks` 最多 8 项，以覆盖记忆数、平均重要度降序、主题名升序稳定排列；多标签记忆可以计入多个主题，domain/lane 父级仍按唯一记忆计数。
5. 所有层级的平均重要度按所属唯一记忆计算。`latest_event_at` 只取 `event_time`，没有已知事件时间就返回 `null`；不得回退到事务时间或主张有效期。
6. CognitiveContext 可选择渲染 outline 与 landmarks，但每个用户可控文本仍需经 `wrap_as_data`；数字摘要继续限定为计算后的数值。

## 后果

- Agent 可先从 domain/lane 定位，再沿已有标签下钻；landmarks 提供跨分轨的高频主题入口。
- 结果是可解释的标签索引，不承诺超越标签质量的语义聚类。未打标签的记忆仍保留在 sections 与父级计数中，并计入 `untagged_count`。
- 输出契约有新增字段与查询参数，因此调用方可渐进升级；`sections` 不删除字段。
- 事件时间未知时路标显示为空，避免误导为“最近发生”。
