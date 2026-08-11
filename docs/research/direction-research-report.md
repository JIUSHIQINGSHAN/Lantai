# 兰台记忆（Lantai）发展方向实地调研报告

> 调研日期：2026-08-11
> 调研方法：外部生态网络检索（标注来源）+ 项目内部现状对照（README / CONTEXT / ADR / CHANGELOG / v0.5 优化方案 / .scratch issue）
> 约束遵守：所有外部事实均来自检索结果并标注来源；厂商自报数据均已注明；检索不到的内容标注「未确认」
> 配套文件：`docs/research/direction-research-prompt.md`（本次调研所用提示词）

---

## 一、摘要（直接回答「往哪走」）

1. **记忆已是 Agent 基础设施的一等公民，但生态仍未收敛**：Mem0（约 62.9K★）与 Zep/Graphiti、Letta、LangMem、Supermemory 等分走不同赌注（被动提取层 / 时序图谱 / 运行时一体 / 框架内嵌 / 托管 API），2026 年尚无统一赢家；同时出现「You don't need a vector database」反叙事（单文件、Markdown、SQL-first 方案走红）。
2. **兰台最大的差异化护城河不是检索，而是「记忆生命周期」**：行业公认的开放难题是选择性遗忘、记忆陈旧、写入精度——而这些恰好是本项目已实现（Ebbinghaus 衰减/归档、人工闸门、锦囊待审队列、Checkpoint 回滚），且业界「没有公共基准」的领域。
3. **中文场景是未被主流生态覆盖的蓝海**：Mem0/LangMem/Supermemory 全部英文优先；兰台已有的 FTS5 trigram + jieba BM25 + bge-m3 中文容错链路，加上中文社区的本地化/隐私/可管理诉求（MemOS、Basic Memory 中文热度），是现实可打的差异化。
4. **MCP 是确定的接入标准，兰台已有先发但工具面太窄**：MCP 生态最热品类就是 memory server（358 个仓库、每周新增 55 个），兰台已具备 MCP + Shell Hook 双形态，但仅 4 个 MCP 工具，工具面扩容（与既有 P1 计划一致）应提前。
5. **评估/自证是下一个竞争点，兰台已走在前列**：外部 benchmark（LoCoMo/LongMemEval/BEAM）厂商分数互相打架且不可复现（Mem0 vs Letta 公开争议、LoCoMo 答案键被审计出 6.4% 错误）；兰台的 dry-run 179 样本管线 + 参数矩阵 + 阴影观测，正好是「可复现自证」打法，应扩展为对外主张的依据。

---

## 二、外部生态扫描表

| 方案 | 定位 | 存储/架构 | 近期动态（2026） | 对兰台的启示 |
|---|---|---|---|---|
| Mem0（62.9K★，Apache-2.0） | 通用记忆层（托管 + 自托管） | 图 + 向量（Qdrant 等） | 2026-04 新算法：单遍分层提取 + 多信号检索，LoCoMo 92.5 / LongMemEval 94.4 / BEAM(1M) 64.1；早前完成约 $24M Series A | 生态最大、接入最易；我们与之正面竞争检索性能无胜算，差异化在生命周期与中文 |
| Zep / Graphiti（29.6K★） | 时序知识图谱 | 图谱 + 向量（Postgres） | LongMemEval 71.2%（gpt-4o）；与 Mem0 有 LoCoMo 分数公开争议 | 「什么变了、什么时候变的」查询是硬需求；兰台已有 Chronos 双时间轴 + supersedes 边，冲突消解层（P0-2）应加强这块 |
| Letta（MemGPT） | Agent 运行时 + 记忆一体 | 块式核心记忆 + 档案 + 文件 | Letta Filesystem 在 LoCoMo 达 74%（「文件系统是否足够？」辩论）；无标准化 benchmark 分数 | 「简单存储够用」论据：兰台 SQLite+FTS5 轻量内嵌路线被间接验证 |
| LangMem | LangGraph 框架内嵌 | 向量 + 属性 | 与 LangGraph 深度绑定，无公开标准分数 | 生态锁定 vs 可移植：兰台走 MCP 可移植是对的 |
| Supermemory | 托管记忆 API（coding agent 向） | 托管（闭源） | LongMemEval-S 81.6%（厂商自报） | 托管闭源与兰台本地优先形成两极；「编码 agent 记忆」需求旺盛（见 §三.3） |
| Cognee | 图原生 ECL 管线 | 图 + 可插拔存储 | 主打 LangGraph/MCP 集成与 14 种检索模式 | 强调「记忆是主动自改进层」——兰台已有演化/提案/结晶规划 |
| Basic Memory / Memvid / ReMe / Memori | 极简/隐私/文件优先 | SQLite + Markdown / 单文件 / SQL | 「不需要向量库」反叙事流行；隐私友好本地化受中文社区关注 | 兰台零依赖内嵌 + 可导出 Markdown 报告（digest）与之同阵营，值得强化「可读可导出」 |
| aiduMEM（上游，MIT） | AI 思想引擎 | mem0 v2 + Qdrant + SQLite FTS5 | 已迭代至 v14.0.1（2026-08-02）Aegis 零硬编码 / v17，新增万神殿联邦；**MCP 服务端仍是 TODO** | 上游转向 mem0/Qdrant 重量级路线，与兰台「明确不用 mem0」的轻量决策分道扬镳；MCP 上兰台已反超上游 |
| agentmemory（26.8K★） | 跨 Agent 记忆 server（MCP） | SQLite + iii-engine | 一个 server 服务 Claude Code / Codex CLI / Cursor / Gemini CLI / Hermes 等 32+ 客户端；四级整合 + 衰减 + 自动遗忘 | 验证「跨客户端一个记忆服务」需求；兰台目前只接了 Hermes，客户端矩阵是扩展方向 |

> 注：star 数来自 2026-08-11 检索结果，会漂移；benchmark 分数除非注明，均为厂商自报，不代表可复现对比。

---

## 三、市场与需求信号

1. **记忆成为平台方角力点**：OpenAI 2026-06-04 发布「Dreaming」——后台自动记忆合成（对标「记忆陈旧」问题），评价维度为 carry forward / follow preferences / stay current（来源：openai.com/index/chatgpt-memory-dreaming/）；Anthropic 已上线 Claude memory（结构化 wiki、nightly 更新），2026-03 免费化并加导入工具。→ 个人场景被大厂原生记忆蚕食，但大厂方案不可审计、不可迁移、不可本地化，正是兰台窗口。
2. **MCP 已成既定标准**：2026-07-28 MCP spec 进入 stateless 化 RC（Google 主导，Hugging Face 等参与），五厂商（Anthropic/OpenAI/Google/Cloudflare/AWS）格局稳定；memory server 是 MCP 生态最热品类（来源：developers.googleblog.com、PT-Edge）。
3. **编码 agent 记忆是增长最快的细分**：claude-code-memory 子类 70+ 仓库、每周新增 19 个；agentmemory、OpenViking（字节）等瞄准「编码 agent 跨会话记忆」（来源：PT-Edge insights / agentmemory README）。
4. **中文社区信号**：MemOS（记忆操作系统）在中文社区热推（3700+★、强调三层记忆架构与知识库共享）；Coze 将「长期记忆」作为智能体卖点；企业平台（Dify/FastGPT 等）以知识库为主、记忆能力浅——「轻量、本地、可导出、中文分词」的组合在中文区仍是空白（来源：53AI 两篇、腾讯云 Agent 平台横评）。
5. **评估市场混乱 = 自证机会**：LoCoMo 被审计出答案键 6.4% 错误、judge 接受率问题；Mem0 与 Letta 就 LoCoMo 分数公开互撕；LongMemEval-S 被指更像 context window 测试；业界公认缺乏写精度/遗忘质量/隐私边界类基准（来源：dev.to LoCoMo 审计、Letta blog、Reddit r/AIMemory）。

---

## 四、趋势判断（含置信度）

1. **「选择性遗忘与记忆陈旧」取代「检索召回」成为记忆系统的主要竞争点**（置信度：高）。依据：OpenAI Dreaming 明确针对 staleness；Mem0 报告把 staleness / temporal abstraction / cross-session identity 列为开放难题；Bessemer 等分析师把 selective forgetting 列为风险；兰台的 Ebbinghaus 衰减+归档正好落在主战场。
2. **记忆服务将 commodity 化，接入走 MCP 标准，价值回到「记忆质量」本身**（置信度：高）。依据：MCP memory server 每周新增 55 个、五厂商协议收敛；memory 仓库碎片化（1,300+ 仓库）是 pre-nucleation 信号。
3. **「context 暴涨」不会消灭外部记忆，反而抬高外部记忆的门槛**（置信度：中）。依据：BEAM 1M/10M token 基准证明纯 context 窗口不可解；但 Mem0 新算法把 token/query 打到 ~6.9K，压缩了「无记忆」方案的借口——外部记忆必须证明自己在写精度、时效、成本上的质量，而不只是「能存能取」。

---

## 五、方向建议

### 立即做（对应既有路线图，先落地，无新风险）

| 项 | 理由（外部证据） | 映射 |
|---|---|---|
| 每日盘点报告（digest） | issue 03 已 ready-for-agent；「可读可导出」正是文件优先/隐私反叙事的卖点；Hermes 早晨注入是真实日常场景 | `.scratch/dialogue-loop/issues/03` |
| Raw Drawer 原文直存 | 编码 agent 场景（代码/日志/配置原文）需求旺盛；「简单存储够用」论据（Letta Filesystem 74% LoCoMo）侧面支持 verbatim 直存 | v0.5 优化方案 P0-1 |
| 冲突消解确定性层 | Zep 唯一显著优势就是时间/关系查询；Chronos + supersedes 已有，补规则层即可把「what changed when」做成卖点 | v0.5 优化方案 P0-2 |
| MCP 工具扩容 + 客户端矩阵 | MCP 是标准且兰台工具面仅 4 个；agentmemory 证明跨客户端（Claude Code/Cursor/Gemini CLI）单 server 模式成立；上游 aiduMEM 的 MCP 仍是 TODO | v0.5 优化方案 P1-5 |

### 一年内（差异化放大器，建议排期时提前）

1. **「遗忘质量」自测体系**：在现有 dry-run 管线上增加「陈旧记忆检索命中率 / 矛盾记忆残留率」指标——对标行业空白（无公共基准），把兰台最强能力变成可复现的数字主张。
2. **中文记忆评测集 + 中文社区发布**：自建中文 LoCoMo 风格小集（面向中文分词/错别字容错），配一版中文 README/发布稿；Mem0 等英文方案不覆盖此场景。
3. **autodream 蒸馏提前**：OpenAI 2026-06 的 Dreaming 恰好验证了「后台记忆合成」方向；既有 P2 计划里的 autodream 与业界方向共振，建议在 Raw Drawer 语料落地后提到 P1 之后立即衔接。
4. **记忆可视化/管理面板（轻量）**：把锦囊队列、Checkpoint、衰减曲线可视化——对应中文社区对「可管理记忆」的诉求（MemOS 卖点）与「可审计」的大厂空白。

### 明确不做（维持既有决策，附新依据）

- **多 Agent 联邦（Pantheon）**：上游 aiduMEM 正在做联邦但未见市场验证；单机单 Agent 场景继续无收益。
- **云端托管 / PyPI 发布 / 引入 mem0+Qdrant**：维持「轻量内嵌、本地优先」定位；Mem0 重量级路线由上游代表即可。
- **大厂原生记忆竞速**：不追 ChatGPT Dreaming / Claude memory 的通用个人记忆，专注「可审计、可迁移、中文、开发者可控」窄面。

---

## 六、风险与应对

| 风险 | 威胁 | 应对 |
|---|---|---|
| 大厂原生记忆（Dreaming/Claude memory）蚕食个人助手场景 | 用户「有记忆就行」时选平台自带 | 强调本地可控、可导出、跨客户端（MCP）；不与大厂拼「通用记忆」，拼「开发者/中文/审计」 |
| Mem0 算法与生态继续碾压 | 通用检索场景无胜算 | 放弃通用检索对标；把生命周期（遗忘/审核/回滚）与中文做成不可替代层 |
| MCP memory server 数量爆炸、价值 commoditize | 沦为又一个 memory server | 差异化回到「记忆质量 + 生命周期 + 自证数据」，MCP 只当接入层 |
| 编码 agent 记忆（agentmemory/OpenViking 等）先占开发者心智 | Hermes 单点接入暴露度不足 | MCP 工具扩容 + 客户端矩阵优先落地（见立即做） |
| 上游 aiduMEM 改名/重构造成认知混淆 | 社区把兰台当 aiduMEM 旧版 | README/发布稿明确「轻量自研存储层」差异；继续维护 CHANGELOG 与移植文档 |

---

## 七、与既有路线图的冲突点

1. **autodream 蒸馏（原 P2）应提前**：外部证据（OpenAI Dreaming、Mem0 报告）表明「后台记忆合成/蒸馏」是 2026 主战场，放在 P2 按需做会错过窗口；建议 Raw Drawer 完成后立即衔接。
2. **MCP 工具扩容（原 P1）建议并入 P0 节奏**：MCP 生态每周 55 个新 memory server 的增速下，「4 个工具」的先发优势保质期很短；扩容应紧跟 P0-1/P0-2 而不是等结晶/树状完成。
3. **v0.5 方案「明确不做」项维持不变**：无新证据推翻 Pantheon/PyPI/mem0 决策，反而上游转向 mem0 强化了轻量差异化。

---

## 八、来源清单（检索日期 2026-08-11）

1. Hamza Shabbir, Agent Memory in 2026 benchmark（2026-06-17）— https://hamzashabbir.dev/article/agent-memory-mem0-vs-letta-vs-zep-vs-langmem-benchmark-2026
2. Digital Applied, Open-Source Agent Memory: Mem0 vs Letta vs Zep（2026-08-04）— https://www.digitalapplied.com/blog/open-source-agent-memory-mem0-letta-zep-compared
3. Mem0 Engineering, AI Agent Memory 2026: Progress Benchmark Report（2026-07-18）— https://mem0.ai/blog/state-of-ai-agent-memory-2026
4. Mem0 GitHub README（New Memory Algorithm, ~62.9K★）— https://www.github.com/mem0ai/mem0
5. Letta, Benchmarking AI Agent Memory: Is a Filesystem All You Need?（2025-08-12）— https://www.letta.com/blog/benchmarking-ai-agent-memory/
6. OpenAI, Dreaming: Better memory for a more helpful ChatGPT（2026-06-04）— https://openai.com/index/chatgpt-memory-dreaming/
7. Google Developers Blog, Scaling AI Agent Infrastructure with the MCP Stateless updates（2026-08-05）— https://developers.googleblog.com/scaling-ai-agent-infrastructure-with-the-mcp-stateless-updates/
8. PT-Edge Insights, Agent Memory in 2026（2026-04-01）— https://mcp.phasetransitions.ai/insights/agent-memory-landscape/
9. XiaomingX, awesome-ai-memory（中文精选列表）— https://github.com/XiaomingX/awesome-ai-memory
10. aiduMEM PyPI v14.0.1 / v17.0.2（2026-08-02）— https://pypi.org/project/aidumem/
11. rohitg00/agentmemory README（2026-02-25）— https://github.com/rohitg00/agentmemory
12. 53AI, 3700+ Star 的 MemOS（2026-01-20）— https://www.53ai.com/news/OpenSourceLLM/2026012069157.html
13. 53AI, Coze 智能体的长期记忆 — https://www.53ai.com/news/coze/2025052746235.html
14. 腾讯云开发者社区, 6 大 AI Agent 平台横评（2026-05）— https://cloud.tencent.com/developer/article/2674338
15. EverMind 官网（LoCoMo 92.73% 自报）— https://evermind.ai/zh
16. arXiv 2512.13564, Memory in the Age of AI Agents — https://arxiv.org/abs/2512.13564
17. arXiv 2604.20006, Benchmarking Long-Term Memory for Personalized Agents（2026-04-21）— https://arxiv.org/html/2604.20006v1
18. Penfield Labs, We audited LoCoMo（2026-04-04）— https://dev.to/penfieldlabs/we-audited-locomo-64-of-the-answer-key-is-wrong-and-the-judge-accepts-up-to-63-of-intentionally-33lg
19. Reddit r/AIMemory, Serious flaws in two popular AI Memory Benchmarks — https://www.reddit.com/r/AIMemory/comments/1s1jlnd/serious_flaws_in_two_popular_ai_memory_benchmarks/

---

## 九、未确认项（诚实标注）

- 各项目 GitHub star 数为检索快照，随日期漂移（Mem0 62.9K、Graphiti 29.6K、agentmemory 26.8K 等）。
- Mem0 / Supermemory / EverMind 等 benchmark 分数为厂商自报，未独立复现；Mem0 与 Letta 就 LoCoMo 分数存在公开争议。
- aiduMEM 的 PyPI 下载量（月 231）仅反映 PyPI 渠道，不代表 GitHub 直接使用量。
- 「每周新增 55 个 MCP memory 仓库」「19 个/周 claude-code-memory」来自 PT-Edge 单篇（2026-04-01），未交叉验证。
