# 兰台记忆（Lantai）发展方向实地调研 — 提示词

> 用途：本提示词由项目所有者用于发起一次「发展方向实地调研」。
> 执行者：Codex（作为调研代理）。执行时需真实检索外部资料，不凭记忆编造。

## 一、调研背景（项目现状速览）

Lantai（兰台记忆）是一个 AI Agent 长期记忆管理系统，Python 3.11 / FastAPI / SQLite+FTS5 / ChromaDB / jieba BM25，
已实现完整链路：摄取（潮波合并+Fastpath 直写）→ 闸门（启发式+LLM 矛盾检测+置信度）→ 演化（Proposal/Checkpoint 回滚）→
遗忘（Ebbinghaus 衰减+归档）→ 检索（向量+BM25+FTS5 trigram+时效 四路混合）。集成形态为 Shell Hook（读）+ MCP（写），
并已落地 Hermes 桌面插件对话自动写入、候选待审队列（锦囊）、检索透明、dry-run 评估管线（179 样本）与 Step7/8 参数
阴影观测与人工验证反馈闭环。安全基线：回环绑定、SSRF 防护、原子备份、端点白名单。原则：人工闸门 / 宁 miss 不脏写 /
零硬编码 / 测试纪律（核心函数不 mock 冒烟测试）。

既有路线图（v0.5 优化方案）：
- P0：Raw Drawer 原文直存（verbatim 记忆）、冲突消解确定性层（规则+LLM 双通道）
- P1：SkillCrystallizer 记忆结晶、TreeMemory 树状组织、MCP 工具扩容
- P2：autodream 蒸馏、Code Graph、Pantheon 联邦（明确不做）
- 待办 issue：每日盘点报告（digest）

## 二、调研目标

回答一个核心问题：**在 2026 年当下的 AI Agent 记忆生态中，兰台记忆应该往哪个方向发展、优先做什么、不做什么。**
要求结论可落地：能映射到具体的功能优先级 / 架构取舍 / 市场定位，而不是泛泛而谈。

## 三、调研范围与方法

### A. 外部生态横向扫描（网络检索，标注日期与来源）
1. 主流记忆方案现状：mem0、Zep、Letta（MemGPT）、LangMem、Supermemory、Cognee、Basic Memory/memdir、
   aiduMEM（本项目上游）等——各自定位（通用记忆层 / agent 框架内嵌 / 个人知识库）、存储架构、近期版本动态。
2. 模型厂与平台方动向：OpenAI / Anthropic / Google 的 memory 产品与接口（如 ChatGPT memory、Anthropic memory tool、
   Gemini），MCP 生态中的 memory server 标准，国内平台（扣子 Coze、Dify、FastGPT、Cherry Studio 等）的记忆能力形态。
3. 评估与基准：LoCoMo 等长期记忆基准、agent memory 评测趋势；「context 增长 vs 外部记忆」之争的最新论据。

### B. 市场与需求信号
4. 开源热度与社区诉求：GitHub star 增长、issue 高频需求（多用户、持久化、隐私本地化、检索质量、成本）；
   中文社区（中文分词/中文场景）对记忆系统的特殊需求信号。
5. 用户场景迁移：个人助手记忆（Hermes/桌面插件）vs 企业多 Agent 记忆 vs 开发者库；单机本地优先 vs 云端。

### C. 内部交叉分析
6. 把外部趋势映射到兰台：SWOT 式对照——我们的差异化护城河（轻量内嵌、本地优先、中文优化、安全、人工闸门、
   完整遗忘/演化语义）在外界变化中是被放大还是被稀释。
7. 风险识别：哪些外部动向（如模型 context 暴涨、MCP 标准吞并、大厂原生记忆）会威胁本项目定位，如何应对。

## 四、输出要求（报告结构）

产出 `docs/research/direction-research-report.md`，包含：
1. 摘要（3-5 条结论，直接回答「往哪走」）
2. 外部生态扫描表（方案 / 定位 / 架构 / 近期动态 / 对我们的启示）
3. 市场与需求信号（附来源）
4. 趋势判断（3 条以内，每条给置信度与依据）
5. 方向建议（分「立即做 / 一年内 / 明确不做」三档，每项注明理由与映射到的现有能力/铁律）
6. 风险与应对
7. 来源清单（URL + 检索日期）

## 五、约束

- 所有外部事实必须来自真实检索结果，标注来源与日期；检索不到的内容明确标注「未确认」，不得编造。
- 结论必须结合本项目既有代码与文档（README / CONTEXT / ADR / CHANGELOG / v0.5 优化方案 / .scratch issue），
  不得脱离项目现实空谈。
- 不修改任何产品代码；本次只产出调研文档（提示词 + 报告）。
- 若发现调研结果与既有路线图冲突，报告中必须明确指出冲突点。
