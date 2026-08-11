# 01 - 零召回率监控 + token 成本估算（可观测性）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory 可观测性（metric-tracking-recall / quota / cost-guard），
把 RetrievalEvent 从「只追加日志」升级为「可监控信号」：零召回率窗口报告（排除系统噪音）、
按 lane/intent 分组定位检索缺口、场景维度命中率（配合 ADR-0012 scene 层）、token 成本粗估。

## 范围

- `RetrievalEvent` 补 `scene_ids` / `estimated_tokens`（迁移 v3→v4）
- `recall_report` 纯函数 + `estimate_tokens`（零依赖粗估）
- `log_retrieval` 埋点：命中 scene 去重 + query/结果 token 估算
- REST `GET /retrieval/recall-report?days=` + MCP `recall_report`

## 验收

1. `estimate_tokens` 纯函数不 mock（CJK 按字、其他按 4 字符/词元）
2. `log_retrieval` 落库 scene_ids 去重 + estimated_tokens
3. 报告排除系统噪音、按 lane/intent 分组、scene 命中率、token 汇总
4. 迁移 v3→v4 幂等、老数据零丢失
5. 全量 pytest 绿

## 相关文件

lantai/observability/recall_report.py、lantai/observability/retrieval_log.py、
lantai/api/routes_retrieval.py、scripts/mcp_server.py、lantai/models/tables.py、
lantai/storage/db.py、tests/test_observability.py

## Answer（2026-08-11 已实现，test_observability.py 5/5 全绿，全量 pytest 通过）

- `estimate_tokens(text)`：CJK 字符 1 token/字，其余 4 字符/词元，零依赖粗估（无 tiktoken）
- `log_retrieval`：结果记忆的 `scene_id` 去重落 `scene_ids`；`estimated_tokens` = 查询 + 注入内容估算
- `recall_report(days)`：窗口聚合 total/system_noise/real/zero/zero_recall_rate、by_lane、by_intent、
  scene（enabled/events/hit/hit_rate）、estimated_tokens（total/avg）；默认窗口 `RECALL_MONITOR_WINDOW_DAYS=7`
- 入口：REST `GET /retrieval/recall-report`、MCP `recall_report`（工具 15 个）
- 迁移 `CURRENT_SCHEMA_VERSION` 3→4：`retrieval_event.scene_ids`（TEXT）+ `estimated_tokens`（INTEGER DEFAULT 0）

验收对照：
1. ✅ estimate_tokens 纯函数冒烟（中文/英文/空串/不足 4 字符）
2. ✅ scene_ids 去重排序 + token 估算落库断言
3. ✅ 报告聚合断言（噪音排除、分组、场景命中率 0.5、token 汇总）
4. ✅ v3→v4 迁移测试（列补齐 + 老数据零丢失）
5. ✅ 全量 pytest 通过（530+ 例）

备注：精确 token 计数（tiktoken）未引入依赖，粗估仅用于观测；后续可观测性升级（quota/cost-guard）另行决策。
