# 06 - VAULT 档案控制台（锦囊队列 + 记忆档案浏览 + 衰减概览）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 aiduMEI v18.2 控制台 VAULT/SETTINGS 面板与方向调研「把锦囊队列、Checkpoint、
衰减曲线可视化」建议：给兰台加第三个数据面板——记忆档案浏览（只读分页 +
lane/status/decay_class 过滤）+ 锦囊待审队列（页内裁决）+ 衰减概览（按
decay_class/lane/status 分布）。延续 /ui/recall、/ui/evolve 的零依赖静态页模式。

## 范围

- `lantai/services/memory_service.py`：`build_memories_page(session, ...)` 纯函数——
  只读分页（limit∈[1,100]、offset≥0，越界 ValueError）、可选 lane/status/decay_class/
  memory_type 过滤、按 updated_at 新→旧 + id 稳定排序、content 截断（content_max
  默认 160，超出加省略号）；`list_memories(...)` 打开默认会话执行。
- `lantai/api/routes_memory.py`：`GET /memories`（受保护，复用 X-API-Key）。
- `lantai/api/routes_ui.py`：`GET /ui/vault` 零依赖静态页——总览卡片（记忆总数/
  active/archived/待审锦囊数）、锦囊队列（内容摘要 + lane + due_at，approve/reject
  页内裁决后刷新）、档案浏览（过滤器 + 表格 + 分页）、衰减概览（by_lane /
  by_decay_class / by_status 条形图）；`/ui` 入口页追加「档案与锦囊」卡片。
- 文档：spec.md 票据清单、CHANGELOG、CONTEXT 词汇表（档案 vault）。

## 验收

1. `build_memories_page` 不 mock 冒烟测试：真实临时 SQLite 直调——排序、过滤、
   分页、截断、越界校验。
2. `GET /memories` 返回分页结构；`GET /ui/vault` 200 + text/html + 面板标记 +
   引用 `/memories` 与 `/candidates/pending`。
3. `/ui` 入口页同时链接追忆漏斗 / 检索质量看板 / 档案与锦囊（更新既有断言）。
4. 全量测试无回归。

## 相关文件

lantai/services/memory_service.py、lantai/api/routes_memory.py、
lantai/api/routes_ui.py、tests/test_vault_panel.py（新）、tests/test_ui_recall.py、
docs/research/direction-research-report.md（§五.4 记忆可视化）


## Answer（2026-08-11 已实现，test_vault_panel.py 5/5 + 全量无回归）

实现内容：
- `memory_service.build_memories_page(session, ...)` 纯函数：只读分页（limit∈[1,100]、
  offset≥0，越界 ValueError）、lane/status/decay_class/memory_type 过滤、updated_at
  新→旧 + id 稳定排序、content 按 content_max（默认 160）截断带省略号；`list_memories`
  打开默认会话执行。
- `routes_memory.py`：`GET /memories`（受保护，复用 X-API-Key，ValueError → 422）。
- `routes_health.py`：`/stats` 附加 `by_decay_class` 聚合（向后兼容）。
- `routes_ui.py`：`_VAULT_HTML` 零依赖静态页——总览卡片（总数/active/archived/待审锦囊）、
  锦囊队列（摘要 + lane + 置信 + due，页内采纳/驳回 `POST /candidates/{id}/review` 后
  刷新）、档案浏览（过滤器 + 表格 + 分页 + 页码）、衰减概览（by_decay_class / by_lane
  条形图）；`/ui` 入口页追加「档案与锦囊」卡片。DOM 全部 textContent 防注入，
  X-API-Key 走 localStorage（与 recall/evolve 同款）。
- 测试：纯函数 3 例（真实临时 SQLite 直调，不 mock：排序/过滤、分页/截断、越界校验）
  + 页面冒烟 1 例 + 端点 1 例（含 422）。

验收对照：
1. ✅ build_memories_page 不 mock 冒烟（排序/过滤/分页/截断/校验）
2. ✅ GET /memories 分页结构；/ui/vault 200 + 标记 + 引用 /memories 与 /candidates/pending
3. ✅ /ui 入口页三面板（test_ui_recall 断言更新）
4. ✅ 全量测试无回归

## Answer（2026-08-11 收尾确认，全量 pytest 580 passed）

- build_memories_page 只读分页/过滤/排序/截断/越界校验已实现，test_vault_panel.py 覆盖（不 mock 冒烟）。
- GET /memories（受保护）+ GET /ui/vault 零依赖静态页（总览卡片/锦囊队列页内裁决/档案浏览/衰减概览条形图）；/ui 入口页三面板链接。
- 验收对照：1. ✅ 2. ✅ 3. ✅ 4. ✅ 全量 580 passed
