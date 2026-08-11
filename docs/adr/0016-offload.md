# ADR-0016: 上下文卸载——长记忆全文落文件，注入摘要 + 路径

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/offload/issues/01-offload-context.md)

## 背景

腾讯 TencentDB Agent Memory `offload_server/compact/`：tiktoken 精确计数 +
mild/aggressive/emergency 三级压缩、tool 头尾保留——目标是「上下文只装得下能
回答当前问题的量」。调研见 `docs/research/tencentdb-agent-memory-borrow.md`。

兰台现状：Shell Hook 已有单条/总字符双预算（ADR-0006 + 召回预算票据），但超长
记忆（如原始文档、长对话记录）仍会占满单条预算，`SHELL_HOOK_MAX_CHARS_PER_MEMORY`
越大上下文越贵，越小信息越碎。

## 决策

| 项 | 决策 |
|----|------|
| 卸载阈值 | `SHELL_HOOK_OFFLOAD_CHARS=2000`：记忆内容超过此长度 → 全文落文件，上下文只注入摘要 + 路径；未超长走原截断注入 |
| 落盘位置 | `docs/memory-offload/{memory_id}.md`（`OFFLOAD_OUTPUT_DIR` 可覆盖；与 digest 的 `_DEFAULT_DIGEST_DIR` 同模式） |
| 注入形态 | `build_offload_inject` 纯函数：`- [score] 截断摘要` + `全文: <绝对路径>` 行；evidence 与注入同源收窄 |
| 取回通道 | MCP `offload_read(memory_id)` 返回完整原文；文件名白名单 + 解析后父目录必须在卸载目录内（防穿越） |
| 失败语义 | 落盘/读取异常静默降级为普通截断注入（「宁 miss 不脏写」：不丢内容、不写脏数据） |
| 工具指南 | 截断指南附「已卸载全文可调用 offload_read 查看」 |

## 理由

- 腾讯三级压缩是全量实现（LLM 压缩 + tiktoken 计费），兰台不需要压缩器——
  全文落文件是「无损卸载」，需要时按 id 取回原文，信息零丢失
- 与召回预算正交：预算管「每条多长」，卸载管「长的别进上下文」；组合后上下文
  只随记忆条数增长，不随单条长度增长
- 纯函数 + 文件副作用分离，核心逻辑可无 mock 冒烟测试；路径安全与 digest 一致

## 代价

- 多一次文件 IO（超长记忆命中时）；卸载目录需纳入备份/清理策略
- 仅 Shell Hook 注入路径生效；REST/MCP search 仍返回全文截断（检索结果本就该带摘要）