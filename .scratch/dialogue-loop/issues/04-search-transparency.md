# 04 - Search Transparency 检索透明

Status: resolved
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

## Answer（2026-08-09 已实现，全量 430 测试全绿）

实现内容：
- 新模块 `remembrance/retrieval/evidence.py::build_evidence(results)`：
  检索结果 → 来源说明 [{id, content[:200], score}]；
  非 rerank 结构直接取 memory.id；rerank 结构（仅 document）按内容反查 DB 拿 id
  （查不到给 None 不阻断）；空/异常 → []，零侵入。
- shell_hook `build_context`：有命中时 context 加「【本次依据】」前缀段
  （每条 `(mem_id, score) 摘要`）+ 返回结构化 `evidence` 字段；
  无命中/异常仍返回 {}（测试覆盖）。
- MCP `search` 与 REST `POST /search` 响应补 `evidence` 字段
  （used_ids 回填通道 event_id 不受影响）。
- 测试：tests/test_evidence.py 5 例（核心函数真实内存 SQLite）+ test_shell_hook.py 2 例
  + test_mcp.py 1 例。

验收对照：
1. ✅ shell_hook 注入含依据段（有命中时，context 前缀 + evidence 字段）
2. ✅ MCP search 响应含来源说明（REST /search 一并补齐）
3. ✅ 无命中/异常静默降级（{} / []，测试覆盖）

实现说明/偏差：
- REST /search 也补 evidence（ticket 范围只提 MCP，相关文件列了 routes_search.py，
  为通道一致性一并补齐）。
