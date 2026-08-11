# 03 - MCP 工具扩容 + 客户端矩阵（第一批）

Status: resolved
Type: task
Source: docs/research/direction-research-report.md「立即做」+ v0.5 优化方案 P1-5（提前）

## 目标

现有 8 工具（search/add/feedback/backfill/add_dialogue/candidates_pending/candidate_review/get_digest）。
本批新增 4 个：raw_add（原文直存）、rollback（回滚）、conflicts_list（冲突账本）、conflict_resolve（裁决冲突）。
保持输入校验 + 异常隔离（现有 MCP 模式）。

## 验收

1. tools/list 数量 8 → 12，新工具名齐全
2. 输入校验：raw_add 空内容 / rollback 空 id → -32602
3. 各新 handler 有冒烟测试（依赖项为已实现的真实 service，不 mock 内部逻辑）

## 相关文件

scripts/mcp_server.py、tests/test_mcp.py
