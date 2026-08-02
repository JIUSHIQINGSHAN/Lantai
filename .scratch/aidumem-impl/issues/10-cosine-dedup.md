# 10 — 余弦去重

**What to build:** 在 MemoryCandidate 创建时、gate 之前做余弦相似度去重——用余弦不用 Jaccard（对中文分词不敏感）。高相似度候选直接 merge/update，不需要再过 gate。预测阈值 0.80/0.65 需实测标定。

**Blocked by:** 02 — 基础设施栈, 09 — 遗忘语义——archived

**Status:** ready-for-agent

- [ ] MemoryCandidate 创建时做余弦相似度检查
- [ ] 去重在 gate 之前执行
- [ ] 高相似度（>merge阈值）直接 merge/update 已有记忆
- [ ] 中相似度（>update阈值）update 已有记忆
- [ ] 不使用 Jaccard，使用余弦相似度
- [ ] E2E 测试：重复内容写入后 merge 而非新建
