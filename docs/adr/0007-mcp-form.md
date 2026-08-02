# ADR-0007: 集成形态——Shell Hook + MCP 并存

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 11](../../.scratch/aidumem-port/issues/11-mcp-server-form.md)

## 决策

两者并存：
- **Shell Hook** 做注入（读），零依赖快路径
- **MCP server** 做操作（写），标准协议，提供 `search`/`add`/`feedback` 三个 tool

## 理由

- Shell Hook 零依赖，适用于支持 pre_llm_call 的 Agent
- MCP 标准协议，适用于 Claude Desktop 等
- 读路径用 Shell Hook（低延迟），写路径用 MCP（标准化）
- 如果只选一个，选 Shell Hook（零依赖优先）

## 相关

- [ADR-0006](0006-shell-hook-contract.md) — Shell Hook 契约细节
- [票据 11](../../.scratch/aidumem-port/issues/11-mcp-server-form.md)
- [票据 12](../../.scratch/aidumem-port/issues/12-manifest-json.md) — manifest.json 不需要（MCP 自带约定）
