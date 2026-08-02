# 遗忘语义：从「归档」改为「只降权不删行」

Type: grilling
Status: resolved
Blocked by: 01

## Question

aiduMEM 的遗忘策略是「只降权不删行」——记忆永远不会被物理删除，只是 decay_score 降到极低。remembrance 当前遗忘逻辑是：working tier 且超过 TTL 且 helpful_count=0 → status 改为 "archived"（软删除）。

需要决定：

1. **是否改为只降权不删**：archived 记忆是否还参与检索？还是完全不参与？
2. **与 lane profile 衰减的关系**：现有 `_lane_strength` 已经做指数衰减（`exp(-days/strength)`），aiduMEM 的衰减模型是否不同？是否需要统一？
3. **GC 策略**：如果永不删行，decay_score 降到多少时可以物理清理？还是真的永不清理？存储成本如何控制？
4. **与 `hybrid_search` 的关系**：archived/极低 decay 的记忆在搜索中如何处理？完全排除还是降权保留？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

四项决议（grilling 2026-08-02 与用户确认，阻塞已随 01 清除）：

### 1. 改为只降权不删 → 是

- archived 记忆不参与检索（搜索时 `WHERE status='active'`），但物理不删
- decay_score 降到极低时自动转 archived

### 2. 与 lane profile 衰减的关系 → 保持现状

- 现有 `_lane_strength` 指数衰减不变
- aiduMEM 的衰减模型和这个本质一样，不需要改

### 3. GC 策略 → 不做 GC，永不物理删除

- 单用户 SQLite 存储量不是瓶颈（10 万条记忆 < 100MB）
- 如果将来需要，加一个 `scripts/gc.py` dry-run 脚本

### 4. archived 在搜索中 → 完全排除

- `hybrid_search` 只查 `status='active'` 的记忆
- archived 不参与，不降权保留

ADR：`docs/adr/0005-forgetting-semantics.md`
