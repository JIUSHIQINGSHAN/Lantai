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

## 相关

- [ADR-0007](0007-mcp-form.md) — MCP server 形态（Shell Hook 是读路径）
- [票据 10](../../.scratch/aidumem-port/issues/10-shell-hook-contract.md)
