# Iteration 07 — 兰台内部代码核验（2026-09-19）

## 类型
survey-internal（本地代码一手核验，非外部检索）

## 核验方法
直接读取 `lantai/models/tables.py`、`lantai/cli/mcp.py`、`lantai/api/routes_ui.py`、`hermes-plugin/`、`scripts/`。

## 发现（对照旧文档的修正）
| 问题 | 旧文档说法 | 代码实况 | 处置 |
|---|---|---|---|
| Q1 双时间轴 | 2026-08 报告称「Chronos 双时间轴 + supersedes 边」 | `MemoryItem` 仅 `created_at`/`last_used_at`/`review_due_at`；**无 event_time/valid_at/invalid_at**。supersedes 仅存在于 `MemoryEdge.relation`（tables.py:267） | temporal_conflict **3.5→3.0**（降分）；双时间差距确认为真且比预期更大 |
| Q2 MCP 工具面 | 2026-08 报告称「仅 4 个 MCP 工具」 | `lantai/cli/mcp.py` 注册 **59 个工具**（serverInfo 0.21.0，stdio），覆盖 search/add/审批/冲突/巩固/探针/图谱/树/结晶/persona/scratchpad 等 | integration **2.5→3.5**（升分）；接入短板在「客户端矩阵/宿主回执」而非工具数 |
| Q6 记忆摘要页 | 未知 | `/ui/vault`（档案+锦囊+衰减概览）+ `/graph` 星图 + 悬镜面板存在——兰台形态的记忆管理页已有 | 关闭 Q6；hindsight：对齐平台级 memory summary 交互仍可加强 |

宿主矩阵实况：Hermes 插件（`hermes-plugin/lantai-hook`）+ `shell_hook.py` + `screenshot_memory.ps1` + MCP stdio。对比 agentmemory 的 32+ 客户端仍是差距。

## 评分
兰台加权总分 **3.52 → 3.56**（temporal -0.06、integration +0.10）。score_delta_sum=1.5。

## 决策
- D13：兰台时间维度欠账=「事件时间与有效时间未入库」，这是 R11 主题综合的最高优先级证据；评估领域应加入「迟到更正/时效查询」用例。
- D14：MCP 工具面已足量，路线图中『工具扩容』应改写为『宿主矩阵与注入回执』。
- D15：内部文档的旧结论（Chronos 双时间轴、4 工具）必须在最终简报中显式更正，防止继续流传。

## 覆盖
systems=20 papers=32 lantai 3.56 score_delta_sum=1.5 roadmap_churn=0
