# 02 - 冲突消解确定性层（规则层 + LLM 层双通道）

Status: resolved
Type: task
Source: docs/research/direction-research-report.md「立即做」+ v0.5 优化方案 P0-2

## 目标

新增 `gate/conflict_rules.py`：互斥规则集（settings 可配，默认状态开关/开关/版本变更）、
变更账本表 `ConflictEvent`（可溯源）；规则命中即确定性冲突（severity=high），
未命中回落现有 LLM `check_contradiction`（降级不阻断）；闸门决策结果不变（ARCHIVE_CONFLICT）。

## 验收

1. check_rules 纯函数：规则命中（双向）/未命中/关闭开关，不 mock
2. decide() 集成：规则命中短路 LLM（真实 DB + 候选/记忆种子，mock 仅外部 LLM）
3. 规则未命中回落 LLM：mock chat_json 返回 contradicts → ARCHIVE_CONFLICT
4. ConflictEvent 账本落库；resolve 入口（REST + MCP）可裁决
5. 新表记录 ADR-0010

## 相关文件

lantai/gate/conflict_rules.py（新）、lantai/models/tables.py、lantai/gate/decision.py、
lantai/services/conflict_service.py（新）、lantai/api/routes_conflicts.py（新）、
lantai/core/settings.py、tests/test_conflict_rules.py（新）、docs/adr/0010-conflict-resolution-layer.md（新）
