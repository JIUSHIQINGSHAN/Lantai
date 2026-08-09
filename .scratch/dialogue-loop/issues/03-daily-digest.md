# 03 - Daily Digest 每日盘点报告

Status: ready-for-agent
Type: task
Blocked by: 02

## 目标

每日生成记忆盘点报告（docs/memory-digest/YYYY-MM-DD.md），Hermes 早晨可读。

## 范围

- 新 worker（APScheduler 每日）：聚合 新增记忆/修改/待审数/归档数/检索统计
- 报告格式：markdown，含当日摘要 + 待审候选提示
- Hermes 注入：shell_hook 首条查询附带 digest 摘要（或 MCP `get_digest` + `GET /digest/today`）
- 依赖 02：待审/归档统计来自候选队列

## 验收

1. 定时任务生成当日报告文件
2. 报告含五项统计且数字正确（不 mock 冒烟）
3. Hermes 早晨首次对话可读到摘要

## 相关文件

remembrance/workers/digest_worker.py（新）、remembrance/core/scheduler.py、
scripts/shell_hook.py、scripts/mcp_server.py、tests/test_digest.py（新）
