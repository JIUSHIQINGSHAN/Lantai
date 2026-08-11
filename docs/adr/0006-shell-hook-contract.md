# ADR-0006: Shell Hook 注入契约

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 10](../../.scratch/aidumem-port/issues/10-shell-hook-contract.md)

## 决策

| 项 | 决策 |
|----|------|
| JSON 形状 | 照搬 aiduMEM：stdin `{user_message, ...}`，stdout `{context: "..."}` 或 `{}` |
| 超时 | 2s 返回空 `{}`，`SHELL_HOOK_TIMEOUT` 可配置 |
| 超短消息 | ≤ 3 字符不注入 |
| 搜索策略 | 固定 top_k=5，不开 rerank |
| 上下文格式 | Markdown 列表带分数：`- [0.92] 内容` |

## 理由

- Hook 要求低延迟，rerank 200-500ms 不可接受
- 部分结果比无结果更危险（误导 LLM），所以超时返回空
- 字符数比 token 简单，无需分词

## 扩展（2026-08-11）：召回预算 + 记忆工具指南

| 项 | 决策 |
|----|------|
| 单条上限 | `SHELL_HOOK_MAX_CHARS_PER_MEMORY`（默认 200，替代硬编码 `[:200]`） |
| 总预算 | `SHELL_HOOK_MAX_TOTAL_CHARS`（默认 1500）——按序装入，超预算丢弃剩余行 |
| 截断安全 | 按码点（code point）截断，不会切开 emoji 代理对；超长附后缀「…（已截断；可用记忆工具查看详情）」 |
| 工具指南 | 有命中时注入末尾附「【记忆使用指南】」：何时主动深挖（search 工具/触发词）、每轮最多检索 3 次、新事实用 add 回写；`SHELL_HOOK_TOOLS_GUIDE` 可关 |
| 来源 | 借鉴 TencentDB Agent Memory `auto-recall`（maxCharsPerMemory / maxTotalRecallChars / 码点截断 / 工具调用指南），调研见 `docs/research/tencentdb-agent-memory-borrow.md` |

## 相关

- [ADR-0007](0007-mcp-form.md) — MCP server 形态（Shell Hook 是读路径）
- [票据 10](../../.scratch/aidumem-port/issues/10-shell-hook-contract.md)
