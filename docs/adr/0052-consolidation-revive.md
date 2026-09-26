# ADR-0052: 起复——巩固撤销与碎片恢复（consolidation revive / unconsolidate）

**日期**: 2026-09-26
**状态**: Accepted
**决策者**: 大哥
**来源**: [ADR-0050](0050-consolidation-audit-gate.md) 决策 5「已知限制（如实登记，另票候选）」；票据 `.scratch/consolidation-rollback/issues/01-p0-consolidation-revive.md`；spec `.scratch/consolidation-rollback/spec.md`

---

## 背景

ADR-0050 把巩固产物从「静默直写」升级为「可裁决、可留痕、可审计」，并在决策 5 如实登记撤销侧缺口：apply 时各实体仅 1 笔 checkpoint（够不到 `rollback` 的 ≥2 笔门槛）、`rollback` 无 supersedes 补偿、笔削六操作不含 `consolidated→active`（`record_ops_service.py:31`）、`unarchive_memory` 守卫仅 archived 可恢复（`:256`）——**期内下线某笔巩固产物只能 retract 主记忆止召回，碎片恢复只剩手改 DB**。

三处缺口同根：写入侧已闭环（生成→提案→apply→留痕），**撤销侧不对称**。本 ADR 补齐该对称面，并把操作命名为**起复**（已登记 `CONTEXT.md` 词汇表）。

## 决策

### 1. 独立服务函数，不改 `rollback` 通用路径

`revive_consolidated(memory_id, *, reason, actor="", session=None) -> dict` 落在 `record_ops_service.py`（笔削家族），**不侵占** `promoter.rollback`。

理由：`rollback`（`promoter.py:423`）是 5 类提案共用的**单实体版本回滚**（按 `memory_id` 取检查点、回写字段）。塞入「撤边 + 恢复他者」会让正确性风险外溢到 merge/deprecate 分支——那些分支没有「一主多碎片」结构，边补偿逻辑对它们是纯负担。巩固撤销的形态（一簇：1 主记忆 + N 碎片 + N 条 supersedes 边）是 consolidation 独有，独立函数最诚实。

### 2. 输入二义性按形态判别（宁 miss 不脏写）

| 输入 | 判别依据 | 动作 |
|---|---|---|
| 碎片 id | `status == "consolidated"` | 恢复 active + FTS/向量重同步 + checkpoint（`trigger="revive"`）+ audit（`action="revive"`） |
| 主记忆 id | `source_ids` 非空（promoter 巩固 apply 的落库标记，`promoter.py:190`） | **撤销全簇**：主记忆置 retracted（复用 retract 索引清理写法）+ 每条目标碎片恢复 active 重同步 + 删除 supersedes 边 + 主记忆 audit（`action="unconsolidate"`）+ 每碎片 audit（`action="revive"`） |
| 两者皆非 | — | `{"ok": False, "error": "invalid target (not consolidated fragment nor consolidation master)"}`，路由层映射 409 |

**判别只看 `source_ids` 不看边**：边可能已被部分清理（手工改库、先前半套撤销），此时「有边 + `source_ids` 非空」的双条件会把一个待补完的簇判成非巩固目标而拒绝——恰是决策 5 欠账①要解决的场景。`source_ids` 是 apply 时由 promoter 写入的持久标记，比边更可靠。

**已被普通撤回过的主记忆同样受理**（补完撤销）：`retract_memory` 只置主记忆状态、不动碎片与边，正是欠账原状。此时走本函数补完（碎片恢复 + 边清零 + 重做索引清理），并在 `warnings` 如实标注「master was already retracted」。索引清理不假设干净——前次 retract 若同步失败会有残留，重做一次并如实回报。

**不猜用户意图**：普通 active 记忆（无 `source_ids`、非 consolidated）走本函数一律拒绝——想撤普通记忆请用 `retract_memory`，想恢复普通撤回请用 `unretract_memory`（仅 admin）。形态判别失败即拒绝，不静默选一条路走。

### 3. 一个事务 + 幂等 + 如实回报

- **事务**：主记忆状态、碎片状态、边删除、checkpoint、audit 同事务；FTS/向量同步失败**不阻断**主语义，如实进 `warnings`（SQL `status` 是检索权威过滤面，既有铁律不变）。
- **幂等**（同 `retract_memory` 先例，只认状态不假设首次同步结果）：簇已撤销（主记忆 retracted 且边清零、无 consolidated 碎片）→ `{"ok": True, "already_revoked": True}`；碎片已 active 且带 `trigger="revive"` checkpoint → `{"ok": True, "already_active": True}`。重复调用不报错、不重复落 audit。碎片幂等标记不用「status==active」——那与普通 active 记忆、晚更正 supersedes 旧值不可区分，会拒错对象。
- **审计不含正文**（既有铁律）：`AUDIT_ACTIONS` 增补 `"revive"` / `"unconsolidate"` 两名，既有六名不动。

### 4. 恢复语义的如实边界

- 碎片折叠**不改内容**（consolidated 只改 `status`），故恢复是真实的；
- 但折叠后碎片若被遗忘/笔削/晚更改变更过，恢复的是**变更后现状**——不宣称「恢复到巩固前现场」（生成时刻基线由提案 `proposed_patch`/`provenance` 承载，与 ADR-0050 决策 3 同口径）；
- 撤销主记忆**不删主记忆行**（retracted 保留供审计），与 `retract_memory` 语义一致。

### 5. 验收口径

1. 撤销后主记忆三面（SQL `status` / FTS / 向量）禁用命中=0；
2. 簇内碎片全部恢复 active 且可召回；
3. supersedes 边清零（该主记忆名下）；
4. 重复撤销幂等；
5. 撤销后对同提案再 apply 被 stale 硬门拒（`promoter.py:154`——evidence 仍 consolidated 的情形由本函数消解，但**已 apply 过的提案不得二次 apply**，由既有提案状态机保证）；
6. 非巩固目标走本函数被拒（409）。

## 影响

- **正向**：ADR-0050 决策 5 欠账 ① 闭环——巩固产物从「只能 retract 半条路」变为「可完整撤销」；维护者误批一笔坏巩固后有确定性的补救路径，不再手改 DB。
- **边界**：不动 `rollback` 通用语义；不动 off/shadow 期存量数据（只提供工具，不批量追溯）；不起新中文名冲突（起复已登记，与拾遗不同域不同义）。
- **回滚面本身的新增操作**：`revive` / `unconsolidate` 两个审计动作名进 `AUDIT_ACTIONS`，既有审计查询面自动可见。

## 相关

- [ADR-0050](0050-consolidation-audit-gate.md) — 沉潜过审三模式（本 ADR 补其撤销侧）
- [ADR-0047](0047-bixiao-fourway-record-lifecycle.md) — 笔削四语义（起复为其家族第七操作）
- [CONTEXT.md](../../CONTEXT.md) — 起复词汇表条目
- [ADR-0013](0013-naming-system.md) — 命名纪律（R4 登记先行）
