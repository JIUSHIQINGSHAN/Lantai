# 12 — MCP server

**What to build:** MCP 协议 server，提供 `search`/`add`/`feedback` 三个 tool。与 Shell Hook 并存：Hook 做读（注入），MCP 做写（操作）。不需要独立 manifest.json（MCP 自带约定）。

**Blocked by:** 11 — Shell Hook 注入

**Status:** ready-for-agent

- [ ] MCP server 提供 `search` tool（调用 /search）
- [ ] MCP server 提供 `add` tool（调用 /add）
- [ ] MCP server 提供 `feedback` tool（调用 /feedback）
- [ ] MCP server 遵循标准协议
- [ ] 不需要独立 manifest.json
