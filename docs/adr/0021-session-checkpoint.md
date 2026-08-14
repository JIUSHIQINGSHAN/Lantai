# ADR-0021: 底本（session checkpoint）——五段会话快照

**日期**: 2026-08-14
**状态**: Accepted
**决策者**: 大哥
**来源**: 白皮书路线图「checkpoint 五段会话快照」（Fog 项，移植 aiduMEM checkpoint.py 窄版）

## 背景

Fog 项「checkpoint 五段会话快照——aiduMEM checkpoint.py 移植」自 v0.3 保留。上游
aiduMEM `ducky/checkpoint.py`（v11 Hyperion「5 段会话快照」）语义已核实现状源码：

- 五段块：`cp_active_intent`（在做）/ `cp_next_action`（下一步）/ `cp_current_work`
  （工作区）/ `cp_key_decisions`（决策）/ `cp_open_notes`（待办）；
- 触发：**上下文压缩时自动写入**，**下次会话启动时注入**；
- 保留最近 5 个会话快照（`MAX_SESSIONS=5`），超过 30 天注入自动标注陈旧
  （`STALENESS_DAYS=30`），内容 < 3 字符不落、截断 600 字符。

兰台现状：MemoryCheckpoint 是**逐记忆变更**的 before/after 快照（回滚用），不覆盖
「会话级状态传承」——会话结束时的意图/现场/决策/待办无法跨会话注入，Agent 每次新会话
从零开始。本 ADR 补齐会话级快照层。

## 决策

| 项 | 决策 |
|----|------|
| 机制名 | **底本**（Diben）：ADR-0013 意象池「底本」= 校勘所据定本——会话快照即下次会话所据的定本（与逐记忆回滚的 MemoryCheckpoint 语义区分，各司其职） |
| 数据模型 | 新表 `session_checkpoint`（id 自增 / session_id / block_key / content / created_at），schema 迁移 v11→v12 |
| 五段块 | 同上游：cp_active_intent / cp_next_action / cp_current_work / cp_key_decisions / cp_open_notes（中文标签：在做/下一步/工作区/决策/待办，无 emoji） |
| 写入 | `write_session_checkpoint(session_id, blocks)`：同 session 重写即替换（upsert）；session_id < 3 字符拒绝；块内容 < `CHECKPOINT_MIN_CONTENT`(3) 不落、> `CHECKPOINT_MAX_CONTENT`(600) 截断（宁 miss 不脏写） |
| 读取 | `get_checkpoint(session_id)` / `get_latest_checkpoint()` |
| 清理 | `cleanup_old_checkpoints(max_sessions=CHECKPOINT_MAX_SESSIONS=5)`：只保留最近 N 个会话快照（快照是记录，删超龄会话快照 ≠ 删记忆本体） |
| 注入 | `inject_checkpoint_context()`：`[Checkpoint · 上次会话]` + 五段行；> `CHECKPOINT_STALENESS_DAYS`(30) 自动标注「⚠️ N天+前，仅供参考」；无快照/无合法块返回空串（零侵入降级） |
| 触发 | v1 提供 REST `POST /checkpoint`（会话结束时宿主调用）+ MCP `checkpoint_write`；Shell Hook 自动注入留后续（Hook 注入体积预算已满，需先定预算再接入，宁 miss 不脏写） |
| 接口 | REST：POST /checkpoint、GET /checkpoint/latest、GET /checkpoint?session_id=、POST /checkpoint/cleanup（受保护）；MCP：`checkpoint_write` / `checkpoint_latest`（工具 40→42） |

## 理由

- 语义取自上游源码实证，非猜测（「五段」= 五块内容，非五阶段流程）；
- 会话状态跨会话传承是 Agent 长期记忆的刚需缺口（上游 v11 起实现，兰台无对应物）；
- 复用既有迁移链 / service 层 / 受保护路由 / MCP 工具面，零破坏；
- 宁 miss 不脏写贯穿：过短不落、非法键拒绝、注入空降级、删除只清超龄会话快照。

## 影响

- `CURRENT_SCHEMA_VERSION` 11 → 12；老库增量建 `session_checkpoint` 表 + 索引。
- `lantai/services/checkpoint_service.py`：validate_blocks（纯函数）/ write / get /
  latest / cleanup / inject。
- settings：`CHECKPOINT_MAX_SESSIONS` / `CHECKPOINT_STALENESS_DAYS` /
  `CHECKPOINT_MIN_CONTENT` / `CHECKPOINT_MAX_CONTENT`。
- 测试 `tests/test_checkpoint_service.py`（纯函数不 mock + 真实 SQLite）；MCP 工具计数
  40→42；迁移断言 v11→v12。
- 已知边界（诚实记录）：Shell Hook 自动注入未接入（预算约束）；五段块语义依赖宿主
  在会话结束时按格式调用写入。

## 相关

- [ADR-0013](0013-naming-system.md) — 「底本」意象登记
- [CONTEXT.md](../../CONTEXT.md) — 词汇表登记
- [ADR-0005](0005-forgetting-semantics.md) — 只降权不删（快照清理语义）
- 上游：monkey2jack/aiduMEI `ducky/checkpoint.py`（v11 Hyperion）
