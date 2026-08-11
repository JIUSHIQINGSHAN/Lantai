# 05 - EVOLVE 检索质量看板（借鉴 aiduMEI v18.2 控制台 EVOLVE 面板）

Status: resolved
Type: task
Blocked by: (none)

## 目标

aiduMEI v18.2 控制台 EVOLVE 面板：7 天检索质量看板（查询数、平均命中、平均得分、
零命中数、进化周期日志与反馈信号）。兰台 v4 已具备 recall_report（零召回率监控）
与 RetrievalEvent 事件流，缺的是可视化。

## 范围

- `lantai/observability/recall_report.py`：新增 `recent_retrieval_events(limit)`
  ——最近 N 条检索事件（新→旧），只读聚合，含噪音标记。
- `lantai/api/routes_retrieval.py`：`GET /retrieval/recent-events?limit=N`。
- `lantai/api/routes_ui.py`：`GET /ui/evolve` 零依赖静态页——总览卡片（真实查询/
  零召回/零召回率/token 粗估/系统噪音/场景命中率）、按 lane 与意图分布条形图、
  最近事件流表格（时间/查询/lane/意图/延迟/结果）；`/ui` 改为入口页同时链接
  追忆漏斗与质量看板。

## 验收

1. recent_retrieval_events 真实 DB 直调（不 mock）：新→旧排序 + 字段齐全。
2. /ui/evolve 200 + 面板标记 + 引用两个数据端点；/ui 入口页含两面板链接。
3. /retrieval/recent-events 返回事件流（含 zero_result）。
4. 全量测试无回归。

## Answer（2026-08-11 已实现）

- recall_report.py：recent_retrieval_events（limit ∈ [1,100] 校验，越界 ValueError）。
- routes_retrieval.py：GET /retrieval/recent-events（受保护，复用 X-API-Key）。
- routes_ui.py：_EVOLVE_HTML（卡片 + 双条形图 + 事件表格，DOM textContent 防注入；
  Promise.all 并行拉取 recall-report 与 recent-events）；_INDEX_HTML 入口页
  （双面板卡片）；/ui 由 307 重定向改为入口页。
- 测试：tests/test_evolve_panel.py 5 例（排序/校验/页面/端点），test_ui_recall.py
  入口页断言同步更新。
- 服务已重启验证：/ui/evolve 200、/retrieval/recent-events 200（真实数据）、
  /ui 200 含两链接。

验收对照：1. ✅ 2. ✅ 3. ✅ 4. ✅ 全量 5xx passed
