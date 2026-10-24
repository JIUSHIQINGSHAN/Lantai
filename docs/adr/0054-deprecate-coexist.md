# ADR-0054: 弃用 COEXIST——从未生效的冲突共存裁决

**日期**: 2026-10-24
**状态**: Accepted
**决策者**: 维护者（授权自主开发；「弃用而非激活」的方向裁定由下述证据链支撑，见 `.scratch/review-fixes-2026-10/spec-batch2.md` §08 裁决记录）
**来源**: 票据 `.scratch/review-fixes-2026-10/issues/08-coexist-dead-branch.md`（票 03 执行中发现）；探针 `.scratch/review-fixes-2026-10/probe_03_gate_ledger.py`

---

## 背景

冲突消解层（[ADR-0010](0010-conflict-resolution-layer.md)）设计了 `ConflictResolution.COEXIST`：两条记忆 scope 的 `task_types` 互不相交时，判定为「适用边界互斥，可共存」，不阻断入库。闸门裁决（`decision.py`）在硬冲突判定后曾引入 `ConflictEngine` 仲裁循环，试图用该机制放行「边界互斥」型冲突。

## 证据（2026-10-24 全仓实证）

1. `decision.py` 的仲裁循环给候选构造的 dummy 记忆 structure 固定为 `{"scope": {"domain": lane}}`，**永远没有 `task_types`**。
2. `conflicts.py` 的 COEXIST 预检要求**双侧** scope 均有非空且互不相交的 `task_types`——单侧缺失即不成立。
3. 全仓扫描（`lantai/` + `tests/`）：除 conflicts.py 的读取逻辑与 decision.py 的 dummy 外，**不存在任何向 scope 写入 `task_types` 的代码**（无提取/解析/导入写入方）。
4. 探针实证：给旧记忆种上 `task_types` 后 `decide()` 仍走 archive_conflict（dummy 侧永远缺）；只有直调引擎、双侧互斥构造才得 COEXIST。
5. 结论：COEXIST 不止从 `decide()` 不可达，而是**自诞生全链路从未生效**（无数据供给方）。「激活」它实际是新建一整套 `task_types` 提取链路（新产品能力），不是修 bug。

## 决策

**弃用并拆除**（方向 b），三处一并清理：

| 位置 | 拆除内容 |
|---|---|
| `lantai/gate/decision.py` | ConflictEngine 仲裁块（cand_dummy 构造 + 循环 + COEXIST 放行 commit）；硬冲突直断 `s.commit()` + ARCHIVE_CONFLICT |
| `lantai/cognition/conflicts.py` | `resolve()` 的 COEXIST 预检分支、`ConflictResolution.COEXIST` 枚举成员、`_generate_reason` 的 COEXIST 文案分支 |
| `tests/cognitive/test_conflict.py` | `test_conflict_engine_coexist`（测的是从未生效的机制） |

六维评分「直断」主体（WIN_A/WIN_B/UNRESOLVED）不动。

**行为零变更论证**：拆除前，`decide()` 的仲裁循环因 dummy 侧永无 `task_types` 而必然 `all_coexist=False` 走 else 分支（commit + 返回 ARCHIVE_CONFLICT）；`engine.resolve` 只读 Evidence 不写、`ConflictEngine` 全仓唯一实例化点即该循环——删除后硬冲突路径逐字节同结果。既有冲突类测试（`test_conflict_rules.py` 等）零改动通过即证。

**重启条件**：若未来建立 `scope.task_types` 提取链路（需维护者单独立项），可凭本 ADR 重启该机制——届时闸门侧不得再用 dummy structure，须透传候选真实 structure。

## 影响

- **正向**：闸门关键路径上少一段每候选必建 `MemoryItem` 假对象的死代码；`ConflictResolution` 枚举与实际语义对齐。
- **边界**：不改变任何既有冲突裁决结果（含 salience 降权、LLM 矛盾检测、`check_unavailable` 早退路径）；`build/lib/` 旧构建产物中的残留随下次构建自然消失。
- **回滚面**：仅本票一个变更集（decision.py + conflicts.py + 1 个测试文件 + 本 ADR）。

## 相关

- [ADR-0010](0010-conflict-resolution-layer.md) — 冲突消解层（COEXIST 的设计源头，本 ADR 弃用其未生效分支）
- [ADR-0020](0020-salience-conflict-demotion.md) — salience 降权（冲突处理的活路径，不受影响）
- 票 03（`review-fixes/2026-10/03`）— 曾为 COEXIST 放行分支补 commit，随死分支一并移除；ARCHIVE_CONFLICT 路径的 commit 保留
