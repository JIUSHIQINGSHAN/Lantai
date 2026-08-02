# Jaccard 三态去重：阈值在中文 embedding 下是否成立

Type: prototype
Status: resolved
Blocked by: 01

## Question

aiduMEM 用 Jaccard 相似度做写入侧三态去重：
- 相似度 ≥ 0.85 → merge（合并到已有记忆）
- 相似度 ≥ 0.70 → update（更新已有记忆）
- 相似度 < 0.70 → insert（新建记忆）

remembrance 当前无写入侧去重——审计发现 `/add` 只做 content_hash 去重（完全相同的内容），不做语义去重。

需要验证：

1. **0.85/0.70 阈值**在 bge-m3 中文 embedding 下是否成立？中文语义边界和英文不同，阈值可能需要调整
2. **Jaccard vs 余弦相似度**：aiduMEM 用 Jaccard，但 remembrance 已有余弦相似度实现。用哪个？
3. **去重时机**：在 gate 之前还是之后？在 coalesce flush 时还是 MemoryCandidate 创建时？

**原型即弃**：用 /prototype 技能，用 bge-m3 对一组中文样本（相似/相关/不相关）跑余弦和 Jaccard，观察分布，标定阈值。

## Answer

设计决议（grilling 2026-08-02 与用户确认，原型实现延至 Phase 3-6，阻塞已随 01 清除）：

### 1. 阈值 → 需实测，预测调低

- 预测 0.85/0.70 在 bge-m3 余弦相似度下可能偏高
- bge-m3 的相似度分布比 Jaccard 更紧凑，可能需要调低到 0.80/0.65
- 原型实测后确认

### 2. Jaccard vs 余弦 → 用余弦相似度

- remembrance 已有实现，且 embedding 余弦比 Jaccard 更准
- Jaccard 对中文分词敏感（又回到分词问题）

### 3. 去重时机 → MemoryCandidate 创建时，gate 之前

- 先去重再闸门——如果和已有记忆高度相似，直接 merge/update，不需要再过 gate
- 在 `/add` 或 coalesce flush 后触发
