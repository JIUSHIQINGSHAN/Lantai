# 04 - Search Transparency 检索透明

Status: ready-for-agent
Type: task
Blocked by: (none)

## 目标

检索注入时显示"本次依据了哪些记忆"，解决不可感知。

## 范围

- shell_hook 注入文本附"依据"段：命中的记忆 id + 内容摘要（event_id 已透出）
- MCP search 响应补"命中来源"说明字段
- 复用 used_ids 回填通道（已实现），展示层补全

## 验收

1. shell_hook 注入含依据段（有命中时）
2. MCP search 响应含来源说明
3. 无命中/异常时静默降级（零侵入）

## 相关文件

scripts/shell_hook.py、remembrance/api/routes_search.py、
scripts/mcp_server.py、tests/test_shell_hook.py、tests/test_routes_retrieval.py
