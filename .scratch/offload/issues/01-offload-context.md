# 01 - 上下文卸载：长记忆全文落文件，上下文只注入摘要 + 路径

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory `offload_server/compact/`（tiktoken 精确计数 + 三级压缩）
的窄版落点：超长记忆全文落文件 `docs/memory-offload/{memory_id}.md`，Shell Hook
上下文只注入「摘要 + 全文路径」，需要时经 MCP `offload_read` 取完整原文——
上下文 token 只随记忆条数增长，不随单条记忆长度增长。

## 范围

- `lantai/services/offload_service.py`：`offload_filename`（白名单）/ `build_offload_inject`
  （摘要行 + 路径行，纯函数）/ `write_offload_file` / `read_offload_file`（目录内路径安全校验）
- `lantai/core/settings.py`：`SHELL_HOOK_OFFLOAD_CHARS`（默认 2000）/ `OFFLOAD_OUTPUT_DIR`
- `scripts/shell_hook.py`：`build_context` 超长记忆分支 → 落盘 + `_format_offload_entry`
  （失败降级为普通截断注入）；截断指南附 offload_read 提示
- `scripts/mcp_server.py`：MCP `offload_read` 工具（工具数 19 → 20）
- 文档：ADR-0016、MCP 客户端矩阵、借鉴报告落地顺序 6、CONTEXT 词汇表、CHANGELOG

## 验收

1. 纯函数 `build_offload_inject` / `offload_filename` 有不 mock 冒烟测试
2. 真实 tmp_path 落盘/读回（不 mock 文件系统）；穿越输入被拒（ValueError）
3. 超长记忆注入行 = `- [score] 摘要` + `全文: <路径>`；evidence 同步收窄
4. 短记忆不落盘（保持原截断注入路径）；卸载失败静默降级为截断注入
5. MCP `offload_read` 返回全文；缺参 -32602；test_mcp 工具数 19 → 20

## 相关文件

lantai/services/offload_service.py、lantai/core/settings.py、scripts/shell_hook.py、
scripts/mcp_server.py、tests/test_offload.py、tests/test_mcp.py、docs/adr/0016-offload.md、
docs/mcp-client-matrix.md、docs/research/tencentdb-agent-memory-borrow.md

## Answer（2026-08-11 已实现，test_offload.py 8/8 + test_mcp.py 26/26 全绿）

实现内容：
- `offload_service.build_offload_inject(content, score, max_chars, suffix, path)`：
  注入块 = `- [score] 截断摘要` + `全文: <绝对路径>` 行；evidence 与注入同源（截断摘要）。
- `write_offload_file` 全文落盘（UTF-8，目录自动建）；`read_offload_file` 白名单文件名 +
  解析后父目录必须在卸载目录内（防穿越）；`offload_filename` 对空值/斜杠/`..` 抛 ValueError。
- shell_hook：`len(m.content) > SHELL_HOOK_OFFLOAD_CHARS`（默认 2000）→ 落盘 + 摘要/路径注入；
  落盘失败静默降级为普通截断注入；截断指南附「已卸载全文可调用 offload_read 查看」。
- MCP：新增 `offload_read(memory_id)`；工具数 19 → 20（test_mcp 同步）。
- 测试：纯函数 2 例 + 文件往返 2 例（真实 tmp_path）+ shell_hook 集成 2 例（真实 SQLite，
  embed 为唯一外部 mock）+ MCP 集成 1 例。

验收对照：
1. ✅ build_offload_inject / offload_filename 冒烟（不 mock）
2. ✅ tmp_path 往返 + 穿越 ValueError
3. ✅ 注入块摘要 + 路径行，evidence 收窄 ≤ 单条预算
4. ✅ 短记忆不落盘测试覆盖；_format_offload_entry 失败降级
5. ✅ MCP offload_read 返回全文 + 缺参 -32602 + 工具数 20