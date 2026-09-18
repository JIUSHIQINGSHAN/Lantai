# ADR 0046: Echo Suppression Time Window

## Context

v022 吸收上游回声抑制（EchoMind/Memmy 融改，M2）时，语义为「删除检索候选中所有与本会话
session_id 相同的记忆」。但兰台的 session 域检索本就先按 `principal.session_id` 圈定候选池
（向量 filters 与 FTS SQL 均按 session 过滤，见 fts.py 的 session 条件）——两道机制叠加后，
开启 `ECHO_SUPPRESS_ENABLED` 会把会话内召回**全部清空**，而非只抑制「刚写进上下文的回声」。
上游原意是抑制刚写入的回声（模型把刚看到的记忆复述回检索结果），不是抹掉会话历史。

## Decision

回声抑制改为**时间窗语义**：仅抑制「本会话在窗口期内新写入」的记忆。

- 新增配置 `ECHO_SUPPRESS_WINDOW_SECONDS`（默认 900 秒）；纳入 `RetrievalParams`
  构造期校验（非正数/非有限值 fail-closed 回默认，与 MMR λ/errsig bonus 同款纪律）。
- 抑制条件：`m.session_id == principal.session_id 且 m.created_at >= now - window`。
  `created_at` 缺失的记忆不抑制（宁 miss 不脏写：只抑制能证明是刚写入的）。
- 窗口外的同会话记忆与其他会话记忆照常召回；开关关闭时行为与引入前完全一致。
- 空 session_id 一律不过滤（沿用上游纪律）。

被否决的替代方案：

1. **仅在无 session 圈定时生效**——兰台 session 域检索是主路径，该方案使开关在主路径
   沦为死代码，不解决任何真实场景。
2. **维持现状并登记已知偏差**——默认关虽无回归，但开关语义与文档/上游意图相悖，
   开启即清空召回是隐藏的行为地雷。
3. **移除该功能**——「刚写入的回声不回捞」对 agent 上下文复述场景仍有真实价值，
   保留但限定到时间窗。

## Consequences

- 检索主路径新增一次 `created_at` 比较（内存中判断，无额外查询），开销可忽略。
- 测试替身必须遵守真实过滤契约（session/lane filters 生效），不再允许「全库命中」
  伪造主路径；否则时间窗语义无法被有效验证。
- 窗口期长短是部署可调参数：过短则回声复现，过长则误伤会话内刚确认的重要记忆。
  默认 900s 覆盖单次任务会话的典型写入-回读间隔。
