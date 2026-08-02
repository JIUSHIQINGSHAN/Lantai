# MCP server 形态：独立 stdio / 仅 Shell Hook / 两者并存

Type: grilling
Status: resolved
Blocked by: —

## Question

remembrance 需要决定集成形态：

1. **独立 MCP stdio server**：标准 MCP 协议，适用于 Claude Desktop 等。需要 `mcp` 包依赖。提供哪些 tool（search/add/feedback）？
2. **仅 Shell Hook**：零依赖，适用于支持 pre_llm_call 的 Agent（如 Hermes）。但只能注入上下文，不能主动操作记忆。
3. **两者并存**：Shell Hook 做注入（读），MCP server 做操作（写）？
4. **或者都不做**：只暴露 REST API，由宿主自行集成？

考虑因素：remembrance 的目标用户是谁？主要接入什么 Agent？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

决议（grilling 2026-08-02 与用户确认）：

### 两者并存

- **Shell Hook** 做注入（读），零依赖快路径
- **MCP server** 做操作（写：add/feedback），标准协议
- MCP 提供 `search`/`add`/`feedback` 三个 tool
- 如果只选一个，选 Shell Hook（零依赖优先）

ADR：`docs/adr/0007-mcp-form.md`
