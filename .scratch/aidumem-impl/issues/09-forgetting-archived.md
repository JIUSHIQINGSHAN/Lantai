# 09 — 遗忘语义——archived

**What to build:** decay_score 降到极低时自动将记忆转为 archived 状态；archived 记忆不参与检索（`hybrid_search` 加 `WHERE status='active'`）；保持现有 `_lane_strength` 指数衰减不变；不做 GC，永不物理删除。

**Blocked by:** 02 — 基础设施栈

**Status:** ready-for-agent

- [ ] decay_score 低于阈值时记忆自动转 archived
- [ ] `hybrid_search` 只查 `status='active'` 的记忆
- [ ] archived 记忆物理不删除
- [ ] 现有 `_lane_strength` 衰减逻辑不变
- [ ] E2E 测试：低 decay 记忆不出现在搜索结果中
