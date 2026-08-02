# Remembrance-System 上下文

## 项目定位

AI Agent 长期记忆管理系统——摄取、闸门、演化、检索、遗忘的完整链路。

## 词汇表

| 术语 | 定义 |
|------|------|
| **lane**（分轨） | 记忆类型分轨：fact / rule / experience / preference / chat / general。每轨有独立的衰减参数和检索权重 |
| **tier**（层级） | 记忆层级：working（工作记忆） / long_term（长期记忆）。working 超过 TTL 且无帮助时归档 |
| **gate**（闸门） | 记忆准入控制：置信度阈值 + 新颖度评分 + 矛盾检测 → 五档决策（reject / working_only / promote_semantic / promote_procedural / archive_conflict） |
| **coalesce**（潮波合并） | 短消息异步缓冲合并，减少 LLM 提取调用次数。缓冲键 = `user_id + lane`，按 lane 分档定义冲刷参数（`LANE_COALESCE_PROFILES`）。`/add` 开关切换（`COALESCE_ENABLED`），一个入口自动分流。见 [ADR-0003](docs/adr/0003-coalesce-buffer-key.md) |
| **fastpath**（白名单直写） | 特定句型绕过 LLM 提取直接写入，原则「宁 miss 不脏写」。三类句型：自我声明/偏好表达/显式指令。正则匹配放 `parsing/fastpath.py`。命中直接返回 `fastpath_candidate`，不入缓冲 |
| **candidate**（候选记忆） | 从 RawDocument 经 LLM 提取的结构化知识，尚未通过闸门。去重（余弦相似度）在 candidate 创建时、gate 之前执行 |
| **proposal**（提案） | 候选记忆通过闸门后生成的变更提案（add/update/merge/deprecate），待应用或拒绝 |
| **checkpoint**（检查点） | 记忆变更前后的快照，用于回滚 |
| **decay_score**（衰减分） | 记忆保持强度，按 lane profile 指数衰减。降到极低时自动转 archived |
| **facade rule**（门面铁律） | 重构约束：只搬家不改语义，旧 import 全绿。见 [ADR-0001](docs/adr/0001-facade-rule.md) |
| **service layer**（service 层） | 路由 handler 下沉的业务逻辑层。handler 只做 HTTP 解析/返回，业务逻辑在 service |
| **archived**（归档记忆） | decay_score 极低后自动转换的记忆状态，不参与检索（`WHERE status='active'`），但物理不删 |
| **Shell Hook** | 零依赖 CLI 注入路径：stdin 收 JSON，stdout 返回 Markdown 上下文。2s 超时返回空。见 [ADR-0006](docs/adr/0006-shell-hook-contract.md) |
| **search_trace** | `/search?trace=true` 返回的每步诊断数组：`{step, elapsed_ms, candidate_count, score_range}`。overhead < 1ms |
