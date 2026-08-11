# TencentDB Agent Memory 调研与可借鉴清单（2026-08-11）

来源：腾讯云数据库团队 2026-05 开源（MIT），v2.0.0 Team Memory；源码克隆自
`https://github.com/TencentCloud/TencentDB-Agent-Memory`（调研时点 2026-08-11）。

## 定位差异

- 兰台：单人记忆引擎，写入侧闭环（摄取→闸门→演化→遗忘→检索）极细。
- 腾讯：团队记忆中枢 + 代理注入管线（Memory Core / Memory Hub / Memory Proxy /
  Memory Knowledge 四组件），装配/消费侧深。

## 高价值借鉴

| 借鉴点 | 腾讯实现 | 兰台落点 |
|--------|----------|----------|
| Skill 资产化 | `MemoryCore/src/core/skill/types.ts`：SKILL.md frontmatter + 资源 + 版本 + 触发边界 + 执行步骤 + 验证规则，单表多版本 | procedura 记忆升级为可注入 Skill（proposal 系统已有底座） |
| L2 场景层 | `core/scene/`：场景聚合，召回时导航全文注入让 LLM 判相关 | 增加 scene 聚合，检索先给导航再下钻 |
| 召回预算 | `core/hooks/auto-recall.ts`：maxCharsPerMemory + maxTotalRecallChars + 码点截断 + 截断后缀 | **已落地（本票）**：shell_hook 双预算 + 后缀 |
| 工具指南 | 注入末尾附 memory_search/conversation_search 指南 + 每轮 ≤3 次搜索上限 | **已落地（本票）**：shell_hook 指南 |
| 上下文卸载 | `offload_server/compact/`：tiktoken 精确计数 + mild/aggressive/emergency 三级压缩 + tool 头尾保留 | 未来：长记忆全文落文件，上下文只注入摘要+路径 |
| 可观测性 | `core/report/metric-tracking-recall`、quota/cost-guard | RetrievalEvent 补零召回率监控 + token 成本估算 |
| 资产绑定 + ACL | Memory Hub Fixed Binding + ACL 四级收窄 | 多 Agent 接入时按 agent_id 绑定 lane 集 |
| mem: 会话指令 | `MemoryProxy/src/mem-command/`：mem:sync / mem:create-skill / mem:help | MCP 命令式工具 |
| provenance | v2.0.1：自定义 prompt + provenance（哪套 prompt/模型/时间产出） | candidate/proposal 记录提取 prompt 版本 |
| LLM-Wiki | `MemoryKnowledge/.../ingest-v2/`：增量维护 + overview.md 综述 + wikilink 下钻 | digest 升级为持续维护的 Wiki |
| 冷启动导入 | 导入仓库/文档/历史 Session，保留原始时间戳 | 批量导入历史会话 JSONL |

## 兰台已有、不必照搬

- 「宁 miss 不脏写」锦囊（pending_review + TTL 归档）比腾讯自动提取更稳。
- 安全基线（SSRF / 备份原子化 / 回环绑定）腾讯开源版没有这么细。
- FTS5 trigram 中文容错、Chronos 时区处理（digest_worker 本地日界换算）。

## 落地顺序建议

1. 召回预算 + 工具指南（已完成，见 `.scratch/recall-budget/issues/01`）
2. Skill 资产化（已有 80% 基础）
3. scene 聚合层（架构性改动，先写 ADR）