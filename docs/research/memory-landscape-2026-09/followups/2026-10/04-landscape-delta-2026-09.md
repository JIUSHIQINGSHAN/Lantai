# 04 · 动态刷新一页：记忆领域 Delta（窗口 2026-09-19 之后，检索 2026-09-25）

版本数据读自各仓库 releases 页（经 GitHub API 核对 tag/日期/要点，非臆测）。

## mem0ai/mem0 —— https://github.com/mem0ai/mem0/releases
- **v2.2.0（2026-09-23，窗口内）**：Python SDK 新增 User Profiles（get/generate/update_profile 等）：按项目 JSON Schema 由 LLM 从记忆生成「单一用户结构化画像」，异步生成、幂等键防重复。同日 ts-v3.3.0（Node SDK 同功能）。
- 同日四个 coding-agent 插件小版本（pi-agent-v0.3.2 / opencode-v0.4.1 / openclaw-v1.2.1 / deepseek-plugin-v0.3.2）：检索策略措辞改为「重复调查或可能依赖早前决策时先搜记忆」，召回策略（smart/always/manual）未变。
- 参考（窗口前）：v2.1.0（2026-09-18）加 surface-identity 请求头（X-Mem0-Source 等）。

## getzep/graphiti —— https://github.com/getzep/graphiti/releases
- 最近一月：v0.30.2（2026-09-08，FalkorDB 更新）、v0.30.0（2026-09-01，修复 Neo4j 查询忽略所配 database 的问题）、mcp-v1.1.0（2026-09-01，同修复，自托管 Neo4j Enterprise 有行为变更）。
- **09-19 之后：无可见变化**（无新 release）。

## letta-ai/letta —— https://github.com/letta-ai/letta/releases
- 最新 release v0.16.8（2026-05-14）；最近一月与窗口内 **无可见变化**（GitHub releases 维度；其他发布渠道未核）。

## volcengine/OpenViking —— https://github.com/volcengine/OpenViking/releases
- **v0.4.21（2026-09-20，窗口内，含破坏性变更）**：存储/队列/PathLock/文件系统稳定性修复；新增 Hermes、MiMo/MiMoCode、WorkBuddy 日志源与独立 Hermes memory provider；**MCP `search` 在默认 list 模式下对 context-only 参数（max_tokens、exclude_uris 等）改报参数错误**，需显式 `mode="context"`；Python SDK 降至 3.8 兼容；Docker 默认工作目录改 `/app/.openviking`。
- 参考（窗口前）：v0.4.20（2026-09-14）、python-sdk@0.1.12（2026-09-18）、sdk/go/v0.0.2（2026-09-10）。

## rohitg00/agentmemory —— https://github.com/rohitg00/agentmemory/releases
- 最新 release v0.9.29（2026-08-16）；最近一月与窗口内 **无可见变化**。

## 2026-09 下旬新论文/新基准（WebSearch 核查）
- **未发现** 09-19 之后可确认的记忆领域新基准论文（检索关键词：agent memory benchmark / arXiv 2609，2026-09-25）。
- 参考项（9 月中旬）：GraMRAG，arXiv:2609.14066（提交 2026-09-12）：多智能体 RAG 用动态多模态记忆图 + RL（拓扑感知策略优化），多步长程推理「SOTA」为论文摘要自述（自报）。来源：https://arxiv.org/abs/2609.14066（检索 2026-09-25）。
- 生态侧（来源各仓库 README，检索 2026-09-25）：HaluMem 中选 EMNLP 2026 Main，2026.09 有社区 EvalPort 适配器（github.com/MemTensor/HaluMem）；MemoryAgentBench 2026-05 增 GPT-5-Mini 结果并推出后继 MemoryArena（ICML 2026，github.com/HUST-AI-HYZ/MemoryAgentBench）。

## 与兰台的关联提示（一句话级）
- OpenViking v0.4.21 的 MCP search 参数严格化与 mem0「按需检索而非每问必搜」的策略措辞，均与兰台召回/注入阶段调参直接相关，建议入观察清单。
