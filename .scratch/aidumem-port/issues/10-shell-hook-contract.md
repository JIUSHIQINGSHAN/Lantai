# Shell Hook 注入契约

Type: grilling
Status: resolved
Blocked by: —

## Question

aiduMEM 通过 Shell Hook（`pre_llm_call`）在 Agent 发送 LLM 请求前注入记忆上下文。需要确定注入契约：

1. **stdin/stdout JSON 形状**：Hook 接收什么格式的输入？返回什么格式？aiduMEM 的形状是否照搬？
2. **2s 超时静默降级**：搜索超过 2s 时返回空上下文还是部分结果？超时阈值是否可配置？
3. **超短消息不注入**：多短的算"超短"？阈值是字符数还是 token 数？
4. **搜索策略**：Hook 用 `/search` 的什么参数？top_k 固定还是动态？是否 rerank？
5. **上下文格式**：注入的记忆以什么格式呈现给 LLM？（Markdown 列表？JSON？带分数标注？）

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

五项决议（grilling 2026-08-02 与用户确认）：

### 1. stdin/stdout JSON 形状 → 照搬 aiduMEM

- stdin 收 `{user_message, ...}`
- stdout 返回 `{context: "..."}` 或 `{}`（无结果）

### 2. 超时降级 → 2s 返回空 {}

- 阈值可配置（`SHELL_HOOK_TIMEOUT`）
- 不返回部分结果——部分结果比无结果更危险（误导 LLM）

### 3. 超短消息 → ≤ 3 字符不注入

- 用字符数不用 token（简单、无需分词）

### 4. 搜索策略 → 固定 top_k=5，不开 rerank

- Hook 要求低延迟，rerank 多 200-500ms 不可接受

### 5. 上下文格式 → Markdown 列表，带分数标注

- `- [0.92] 用户偏好Python`
- 简洁、LLM 友好

ADR：`docs/adr/0006-shell-hook-contract.md`
