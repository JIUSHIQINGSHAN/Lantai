# ADR-0053: 提案裁决时刻——`MemoryProposal.decided_at`

**日期**: 2026-09-26
**状态**: Accepted
**决策者**: 大哥
**来源**: [ADR-0050](0050-consolidation-audit-gate.md) 边界「冷却期起算点用 `created_at` 近似」条（:109）；票据 `.scratch/consolidation-rollback/issues/03-p0-decided-at-column.md`；spec `.scratch/consolidation-rollback/spec.md`

---

## 背景

[ADR-0050](0050-consolidation-audit-gate.md) 决策 3 的拒绝冷却（`CONSOLIDATION_REJECTED_COOLDOWN_DAYS=30`）只抑制**生成侧**重复奏，防「拒绝一次＝订阅每日重复打扰」。但其起算点只能取 `created_at`（提案生成时刻）近似裁决时刻——`MemoryProposal` 无裁决时刻列：`applied_at` 仅 apply 时写、reject 不写，`decide_proposal` 拒绝分支只置 status/decided_by/decision_reason。

**可达失败场景**：提案 pending 逾冷却期（>30 天）后方被拒绝 → 冷却窗口自生成时刻起算、早已过期 → 次夜即重新提纯并再生成同簇提案。正是本冷却要消灭的打扰在该窗口内复原，代价为一次多余 LLM 提纯与一次打扰。

ADR-0050 已如实定性：不脏写（无主记忆误落、无碎片误折叠，属「宁 miss」方向降级），冷却是运营优化非安全门。**但起算点错是可修的口径缺陷，非设计取舍**——本 ADR 修它。

## 决策

### 1. 新增 nullable 列，老行不回填

`MemoryProposal.decided_at: datetime | None = None` + 迁移 v23→v24（`_has_column` 幂等守卫 + `ALTER TABLE`，无索引——不参与检索热路径，只服务冷却判定）。

- **nullable 无默认**：NULL 是「未记录」的**事实状态**，不是待填的坑。宁 miss 不猜。
- **老行不回填**：拿 `created_at` 冒充 `decided_at` 会让冷却期起算点悄悄失真（恰是本 ADR 要消灭的偏差），且把「猜」写进数据库不可撤销。老行 `decided_at IS NULL`，读侧回退 `created_at`——旧口径逐字节不变。

### 2. 裁决即落时刻，不等 apply

三个落点，全部与对应终态同事务：

| 落点 | 位置 | 终态 |
|---|---|---|
| 人工 approve | `evolution_service.decide_proposal` | APPROVED |
| 人工 reject | 同函数 | REJECTED |
| 系统 stale 硬门 | `promoter.apply_proposal` | REJECTED（ADR-0050 决策 3 硬门：任一 evidence 已 consolidated） |

`decided_at` = **人/系统做出决定的时刻**，与 `applied_at`（apply 执行时刻）正交：approve 后 apply 失败/未跑，两列一有一无，各有其义。

### 3. 冷却读侧 `decided_at or created_at`

`_consolidation_proposal_blocked` 改读 `decided_at or created_at`：

- 新行精确（裁决时刻起算，pending 多久都不影响冷却窗口）；
- 老行自动回退旧口径（行为逐字节不变，既有 dedup/cooldown 用例零改动通过即证）。

## 影响

- **正向**：ADR-0050 边界该条闭环——「pending 逾冷却期后方被拒」窗口内的冷却失效修复；维护者迟裁不再被每日重复打扰。
- **边界**：不改 `applied_at` 既有语义；不加索引、不回填；不动其他 proposal_type 的裁决路径落点以外的行为。
- **回滚面**：`decided_at` 只被冷却读侧消费，无级联影响；迁移幂等可重放。

## 相关

- [ADR-0050](0050-consolidation-audit-gate.md) — 沉潜过审三模式（本 ADR 修其冷却期口径）
- [ADR-0052](0052-consolidation-revive.md) — 起复（同波票 01/02）
- [CONTEXT.md](../../CONTEXT.md) — 技术字段登记（decided_at 属 proposal 内部字段，不入词汇表）
