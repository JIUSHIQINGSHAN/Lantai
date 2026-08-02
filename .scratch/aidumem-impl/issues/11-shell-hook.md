# 11 — Shell Hook 注入

**What to build:** 零依赖 CLI 注入路径——stdin 收 `{user_message, ...}` JSON，stdout 返回 `{context: "..."}` 或 `{}`（无结果）。2 秒超时返回空 `{}`（`SHELL_HOOK_TIMEOUT` 可配置），不返回部分结果。≤ 3 字符不注入。固定 top_k=5，不开 rerank。返回 Markdown 列表格式带分数标注：`- [0.92] 内容`。

**Blocked by:** 06 — search_trace 诊断

**Status:** ready-for-agent

- [ ] CLI 脚本存在，stdin JSON → stdout Markdown 上下文
- [ ] 2s 超时返回空 `{}`
- [ ] ≤ 3 字符输入不注入
- [ ] top_k=5 固定，不开 rerank
- [ ] 返回 Markdown 列表带分数标注
- [ ] E2E 测试：正常输入返回上下文，超时返回空
