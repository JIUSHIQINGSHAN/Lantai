# Zep: A Temporal Knowledge Graph Architecture for Agent Memory 精读笔记

> 精读日期：2026-08-11
> 来源链接：https://arxiv.org/html/2501.13956 （镜像：https://ar5iv.labs.arxiv.org/html/2501.13956 ）
> arXiv ID：2501.13956

## 1. 元信息

- **标题**：Zep: A Temporal Knowledge Graph Architecture for Agent Memory
- **作者**：Preston Rasmussen、Pavlo Paliychuk、Travis Beauvais、Jack Ryan、Daniel Chalef
- **机构**：Zep AI（五位作者均属该公司）
- **年份**：2025（arXiv v1 提交时间 2025-01-20）
- **发表地**：arXiv 预印本（cs.CL 分类），CC BY-NC-SA 4.0 许可
- **arXiv ID**：2501.13956
- **全文链接**：https://arxiv.org/html/2501.13956

## 2. 一句话核心贡献

提出 Zep——一个由 Graphiti 时序知识图谱引擎驱动的 AI agent 记忆层服务：以「episode（原始非有损）→ 语义实体/事实 → 社区摘要」三层子图 + 双时态建模（事件时间 T 与事务时间 T′）+「新边使旧边失效（edge invalidation）」机制，把持续演化的对话与业务数据动态合成为可查询、可追溯、带有效期的记忆，在 DMR 与 LongMemEval 两个基准上超过 MemGPT 等基线，并将响应延迟降低约 90%（原文）。

## 3. 研究问题与动机

- LLM 能力受限于上下文窗口、上下文有效利用率和预训练知识，需要外部补充 OOD 知识与减少幻觉（原文 §1）。
- 现有 RAG 面向「大体静态语料」——文档加入后很少变化；而 agent 走向普及需要「持续演化的数据」（用户交互、业务数据、世界数据）作为记忆，整段对话历史与业务数据集放不进上下文窗口（原文 §1）。
- MemGPT 已探索「给 LLM 加记忆」，但作者认为当前 RAG 方式不适合上述未来；知识图谱已被用于增强 RAG（如 GraphRAG），本文在此基础上做「动态 + 时序」化（原文 §1）。
- 作者强调 Zep 是生产系统，重点在检索的准确率、延迟与可扩展性，并用 DMR（来自 MemGPT）与 LongMemEval 两个现有基准评估（原文 §1）。

## 4. 方法/系统设计（逐步细节）

### 4.1 三层知识图谱架构（原文 §2）

图定义为 $\mathcal{G}=(\mathcal{N},\mathcal{E},\phi)$（节点、边、形式关联函数 $\phi:\mathcal{E}\to\mathcal{N}\times\mathcal{N}$），含三个层级子图：

- **Episode 子图** $\mathcal{G}_e$：episode 节点存放原始输入（消息/文本/JSON），是「非有损」数据源；episodic 边连接 episode 与其引用的语义实体。
- **语义实体子图** $\mathcal{G}_s$：实体节点由 episode 提取并经既有图消解；语义边表示实体间关系（facts）。
- **社区子图** $\mathcal{G}_c$：强连通实体簇的高层抽象，含簇级摘要，代表对 $\mathcal{G}_s$ 结构的更全局视图。

设计动机：episode 与语义双存储对应人类「情景记忆 vs 语义记忆」的心理学模型（引 [8]），借鉴 AriGraph [9]；社区层借鉴 GraphRAG [4] 的全局理解，层级「episode→facts→entities→communities」扩展了层级 RAG 策略 [10][11]（原文 §2）。

### 4.2 Episode 与双时态模型（原文 §2.1）

- Episode 三种类型：message / text / JSON；本文实验聚焦 message。一条 message = 相对短文本 + 产生该话语的 actor（说话人）。
- 每条消息带 **reference timestamp** $t_{\text{ref}}$（消息发送时间），用于解析相对/部分日期（如 "next Thursday"、"in two weeks"、"last summer"）。
- **双时态模型**：时间线 $T$ = 事件发生的时间顺序；时间线 $T'$ = Zep 数据摄入的事务顺序。$T'$ 用于传统数据库审计，$T$ 用于建模对话/记忆的动态本质。作者称这是 LLM 知识图谱构建中的一个新颖进展（原文 §2.1）。
- episode 与其派生语义边维护**双向索引**：语义产物可溯源到来源 episode（引用/引述），episode 也能快速取回相关实体与事实；支撑非有损设计（原文 §2.1）。

### 4.3 实体提取与消解（原文 §2.2.1）

- 提取阶段：处理当前消息 + 最后 $n$ 条消息作为 NER 上下文，本文 $n=4$（两个完整对话轮次）；speaker 自动作为实体；随后用类似 Reflexion [12] 的**反思（reflection）**技术减少幻觉、提升提取覆盖率；同时从 episode 提取实体摘要用于后续消解与检索。
- 消解阶段：实体名嵌入到 **1024 维**向量空间，用 cosine 相似度在图内找相似节点；另做一次既有实体名+摘要的全文搜索补候选；候选与 episode 上下文一起交给 LLM 消解 prompt；判定重复时生成更新后的名称与摘要。
- 写入：用**预定义 Cypher 查询**入库，而非 LLM 生成查询，以保证 schema 一致、减少幻觉（原文 §2.2.1）。

### 4.4 Fact 提取与去重（原文 §2.2.2）

- 从当前消息提取实体间事实（每个 fact 含关键谓词 relation_type，如 LOVES / WORKS_FOR）。同一事实可在多实体间重复提取，通过**超边（hyper-edge）**建模多实体事实。
- 提取后为 facts 生成 embedding；边去重流程类似实体消解，且**混合搜索被约束在「同一实体对之间的既有边」**——既防止不同实体对之间相似边的错误组合，又显著降低去重计算量（原文 §2.2.2）。

### 4.5 时序提取与边失效（详见第 5 节，重点）

### 4.6 社区构建与增量更新（原文 §2.3）

- 社区检测借鉴 GraphRAG [4]，但用**标签传播（label propagation）**[13] 而非 Leiden 算法 [14]，理由是标签传播有直接的动态扩展：新实体节点加入时，查看其邻居社区，把新节点归入邻居多数（plurality）社区，并更新社区摘要与图。
- 代价：动态更新的社区会逐渐偏离完整标签传播的结果，因此仍需**周期性社区刷新**；但动态策略显著降低延迟与 LLM 推理成本（原文 §2.3）。
- 社区节点内容：按 [4] 用迭代 map-reduce 式摘要；但与 GraphRAG 不同，本文为检索生成**社区名**（含社区摘要中的关键术语与主题），嵌入存储以支持 cosine 检索（原文 §2.3）。

### 4.7 检索管线（原文 §3）

检索 API 实现 $f:S\to S$：查询文本 $\alpha$ → 上下文文本 $\beta$，由三步复合 $f(\alpha)=\chi(\rho(\varphi(\alpha)))=\beta$：

1. **Search** $\varphi$：三种搜索函数——
   - cosine 语义相似度 $\varphi_{\text{cos}}$（向量）；
   - Okapi **BM25** 全文搜索 $\varphi_{\text{bm25}}$（前两者基于 Neo4j 对 Lucene 的实现 [15][16]）；
   - **广度优先搜索** $\varphi_{\text{bfs}}$（n-hop 内补节点与边；可接受节点作参数，例如用最近 episode 作种子，把最近提到的实体/关系纳入检索）。
   - 搜索字段：$\mathcal{E}_s$ 搜 fact 字段、$\mathcal{N}_s$ 搜实体名、$\mathcal{N}_c$ 搜社区名（社区名=社区覆盖的关键词短语）。社区搜索思路与 LightRAG [17] 的 high-level key 搜索平行（原文 §3.1）。
2. **Reranker** $\rho$：支持 RRF [20]、MMR [21]；另有 Zep 自研的**图结构 episode-mentions 重排器**（按会话中实体/事实被提及频率排序）与**节点距离重排器**（按与质心节点的图距离排序，局部化上下文）；最复杂的是 **cross-encoder**（LLM 用交叉注意力对节点/边与查询打分，成本最高）（原文 §3.2）。
3. **Constructor** $\chi$：把结果格式化为上下文模板。对每条边输出 fact 与 $(t_{\text{valid}}, t_{\text{invalid}})$ 字段；对实体输出 name+summary；对社区输出 summary。上下文模板示例：「FACTS and ENTITIES … format: FACT (Date range: from - to)」（原文 §3）。

## 5. 记忆更新/时效机制深挖（本任务重点）

### 5.1 四时间戳体系（原文 §2.2.3 + 附录 6.1.5）

- 用 $t_{\text{ref}}$ 从 episode 上下文提取事实的时序信息：既支持绝对时间戳（如 "Alan Turing was born on June 23, 1912"），也支持相对时间戳（如 "I started my new job two weeks ago"）→ 解析为绝对 datetime。
- 双时态下跟踪**四个时间戳**，存在边上：
  - $t'_{\text{created}}$、$t'_{\text{expired}} \in T'$：事实在系统中被创建/被失效（事务）的时间；
  - $t_{\text{valid}}$、$t_{\text{invalid}} \in T$：事实在现实中成立的时间范围。
- 附录术语为 `valid_at` / `invalid_at`（「关系变为真的时刻」/「关系停止为真的时刻」），与正文 $t_{\text{valid}}$ / $t_{\text{invalid}}$ 指同一概念（原文如此，术语不完全统一）。

### 5.2 invalidates 机制：新边使旧边失效（原文 §2.2.3，本任务核心）

原文逐句依据：

- 「新边的引入可以使数据库中的既有边失效（The introduction of new edges can invalidate existing edges in the database）」
- 系统用 **LLM 将新边与语义相关的既有边比较**，识别潜在矛盾；
- 当识别出**时间上重叠的矛盾（temporally overlapping contradictions）**时，把受影响旧边的 $t_{\text{invalid}}$ **设为新边的 $t_{\text{valid}}$**（截断旧边有效期，而非删除）；
- 沿事务时间线 $T'$，**Graphiti 在失效判定中始终优先新信息（consistently prioritizes new information）**。
- 结果：图随对话演化动态加数据，同时保留「当前关系状态」与「关系随时间的演化历史」——即非删除、非有损的更新（原文 §2.2.3）。

要点提炼：失效 = 时间区间截断 + 新旧并存；触发条件 = LLM 判矛盾 + 时间重叠；优先级 = 新信息胜出；旧边靠 $t_{\text{invalid}}$ 标记过期而非物理删除。

### 5.3 时效如何驱动查询（原文 §3）

- Constructor 把每条 fact 的 (valid_at, invalid_at) 显式放进检索上下文：「These are the most relevant facts and their valid date ranges. If the fact is about an event, the event takes place during this time. / format: FACT (Date range: from - to)」——时间范围随上下文交给生成模型做时效推理，而不是靠硬过滤。
- 实验观察到：较弱模型（gpt-4o-mini）对 Zep 的时序数据理解仍有不足，「additional development may be needed to improve less capable models' understanding of Zep's temporal data」（原文 §4.3.2）。

### 5.4 反思/蒸馏/摘要类机制

- **实体级反思**：实体提取后采用 reflexion 式反思减少幻觉、提升覆盖率（原文 §2.2.1）——这是「提取质量自检」，不是对话摘要。
- **摘要类**：实体摘要（提取自 episode）；社区摘要（map-reduce 迭代总结，借鉴 GraphRAG）。
- **明确的反例**：会话级「对话摘要」不是 Zep 的构建机制——论文实验里 conversation/session summaries 是作为**基线**（DMR 78.6%、LME 无摘要基线）与 Zep 对比，Zep 本身靠非有损 episode + 语义边检索。原文没有「对整段对话做蒸馏摘要」的机制描述。

### 5.5 冲突/过时信息与审查/安全

- 冲突/过时：只有 §5.2 的 LLM 矛盾比对 + 时间重叠判定 + $t_{\text{invalid}}$ 截断；旧边保留历史。无显式删除、无人工裁决流程、无「低置信度处理」策略描述。
- 审查/安全：**全文没有任何隐私、审查、安全、权限相关章节或描述**（原文未涉及）。

## 6. 实验与结果

### 6.1 实验设置（原文 §4.1、§4）

- 模型：BGE-m3（BAAI）用于 rerank 与 embedding [23][24]；图构建用 gpt-4o-mini-2024-07-18；回答生成用 gpt-4o-mini-2024-07-18 与 gpt-4o-2024-11-20；为对齐 MemGPT 的 DMR 结果另用 gpt-4-turbo-2024-04-09。
- 两实验均把对话历史经 Zep API 写入知识图谱；DMR 检索 top-10 相关边与实体节点，LME 检索 top-20（原文 §4）；结果拼成上下文串喂给 agent。
- LME 实验时间 2024-12 至 2025-01；测试机为波士顿居民区的一台消费级笔记本，连 AWS us-west-2 的 Zep 服务（基线无此网络延迟）；答案评估用 GPT-4o + [7] 的问题专属 prompt（原文 §4.3）。

### 6.2 DMR（原文 §4.2）

- DMR = 500 段多会话对话，每段 5 个 session、每 session 至多 12 条消息，各含 1 组问答对；MemGPT 报 93.4%（gpt-4-turbo），递归摘要基线 35.3%（原文）。
- Zep：gpt-4-turbo **94.8%**、gpt-4o-mini **98.2%**；对照 full-conversation 94.4%/98.0%、session summaries 78.6%/88.0%（原文 Table 1）。作者未能用 gpt-4o-mini 复现 MemGPT 结果（其论文方法细节不足）。
- 作者批评 DMR：每段仅 60 条消息、单轮事实检索、问题含糊（如 "favorite drink to relax with"、"weird hobby" 未被对话明确刻画）、不能代表企业场景；full-context 高得分进一步说明其不足以评测记忆系统（原文 §4.2）。

### 6.3 LongMemEval（原文 §4.3、Table 2/3）

- LME 对话平均约 **115,000 tokens**；六类问题：single-session-user / single-session-assistant / single-session-preference / multi-session / knowledge-update / temporal-reasoning（原文）。
- MemGPT 对比失败：MemGPT 不支持直接摄入既有消息历史，workaround（把消息加入 archival history）后仍无法得到成功回答，故无 MemGPT×LME 数据（原文 §4.3.1）。
- 总体结果（原文 Table 2，标注「原文」）：

| Memory | Model | Score | Latency | Latency IQR | Avg Context Tokens |
|---|---|---|---|---|---|
| Full-context | gpt-4o-mini | 55.4% | 31.3 s | 8.76 s | 115k |
| Zep | gpt-4o-mini | 63.8% | 3.20 s | 1.31 s | 1.6k |
| Full-context | gpt-4o | 60.2% | 28.9 s | 6.01 s | 115k |
| Zep | gpt-4o | 71.2% | 2.58 s | 0.684 s | 1.6k |

- 准确率提升：gpt-4o-mini +15.2%，gpt-4o +18.5%（与摘要「up to 18.5%」一致）；延迟约降 90%，上下文 token 从 115k 压到 1.6k（原文）。
- 按题型（原文 Table 3）：
  - gpt-4o-mini：single-session-preference 30.0%→53.3%（+77.7%）、temporal-reasoning 36.5%→54.1%（+48.2%）、multi-session 40.6%→47.4%（+16.7%）、single-session-user 81.4%→92.9%（+14.1%）提升；single-session-assistant 81.8%→75.0%（-9.06%）、knowledge-update 76.9%→74.4%（-3.36%）下降。
  - gpt-4o：single-session-preference 20.0%→56.7%（+184%）、temporal-reasoning 45.1%→62.4%（+38.4%）、multi-session 44.3%→57.9%（+30.7%）、knowledge-update 78.2%→83.3%（+6.52%）、single-session-user 81.4%→92.9%（+14.1%）提升；single-session-assistant 94.6%→80.4%（-17.7%）下降。
- 结论：更复杂/细微的题型在更强模型下提升最明显；single-session-assistant 类下降是「notable exception」，需要进一步研究与工程（原文 §4.3.2）。

## 7. 局限与疑点

### 论文自认的局限（原文）

1. DMR 规模小、设计有缺陷：单轮事实检索、问题含糊、60 条消息可塞进上下文、不代表企业场景（§4.2）。
2. MemGPT 无法在 LongMemEval 上对比（不支持直接摄入历史消息），缺少同场对比数据（§4.3.1）。
3. 动态社区扩展会逐渐偏离完整标签传播结果，需周期刷新（§2.3）。
4. single-session-assistant 类问题性能下降（gpt-4o -17.7%、gpt-4o-mini -9.06%），机制未明（§4.3.2）。
5. 较弱模型对时序数据的理解不足（§4.3.2）。
6. 未纳入领域本体（ontology）与 fine-tuned 提取模型（Triplex 等），列为 future work（§5）。
7. 现有基准不足：缺少企业/客户体验类 memory 基准；没有基准能评估「对话 + 结构化业务数据」合成能力；传统 RAG 能力未在 BEIR/FinanceBench 等上评测（§5）。
8. 生产可扩展性的成本/延迟讨论不足，仅给出了检索延迟（§5）。

### 我读到的可疑/含糊处

- **边失效无独立评估**：全文没有任何针对 invalidation 正确率/误判率的实验或消融，核心机制缺乏量化验证。
- **无消融**：三种搜索（cos/BM25/BFS）、各 reranker（RRF/MMR/episode-mentions/节点距离/cross-encoder）对最终分数的贡献均未单独评测。
- **细节缺失**：$t'_{\text{expired}}$ 除失效场景外何时设置未说明；无显式删除/修正流程描述；实体消解、fact 去重、矛盾判定的 LLM 失败率未披露；Cypher 预定义查询的具体内容未给。
- **top-k 不一致**：DMR 检索 top-10、LME 检索 top-20，未说明理由（原文如此）。
- **术语不统一**：正文 $t_{\text{valid}}/t_{\text{invalid}}$ vs 附录 valid_at/invalid_at（原文如此）；「LongMemEval${s}$」中的 ${s}$ 是 LaTeXML 注记渲染残留（原文如此）。
- **排版怪癖**：§2.2.1 段首「ntity extraction represents…」、§2.2.2 段首「or each fact containing its key predicate.」缺首字母——arXiv HTML 原文如此，非转录错误。
- **无图**：全文 3 张表格、0 张架构图；架构只能靠文字。
- **时区/网络**：实验在波士顿（美东时区）连 AWS us-west-2，论文未讨论时区差异对时序抽取/基准的影响（未确认）。
- **安全维度缺失**：隐私、审查、权限、数据删除合规完全未涉及（原文未涉及）。

## 8. 对兰台反思模块的启示

兰台现有链路映射：`Chronos` 的 `valid_from/valid_to`（tables.py）≈ $t_{\text{valid}}/t_{\text{invalid}}$；`supersedes` 关系（relation 枚举）≈「新边取代旧边」；`conflict_event` 账本 ≈ 事务时间线 $T'$ 的审计维度；`proposal(deprecate)` ≈ 失效动作；`hybrid_search`/BM25 检索 ≈ $\varphi$ 三路搜索。以下 6 条具体可借鉴点：

1. **「时间对齐截断」式 deprecate（最重要）**：Graphiti 把旧边 $t_{\text{invalid}}$ 设为新边 $t_{\text{valid}}$，旧记录保留、仅截断有效期。兰台 `proposal(deprecate)` 可改为「deprecate = 将旧记忆的 `valid_to` 设为新记忆的 `valid_from`」并保留 `supersedes` 指针，而不是整体作废；只对「时间重叠且矛盾」的旧事实做此操作，时间不重叠的事实应并存（避免误杀历史事实）。
2. **双时态分离，驱动过期检测**：兰台已有 `valid_from/valid_to`（事件时间 $T$）与 `conflict_event`/`created_at` 类字段（事务时间 $T'$），应显式区分「事实成立时间」与「系统写入时间」；过期检测应基于 $T$（`valid_to < now`），社区/摘要等派生物的「过期」则应基于 $T'$（多久没被新数据触碰）。Graphiti 用 $T'$ 做失效优先级、用 $T$ 做查询时效，这一分工可直接照搬。
3. **候选矛盾对召回 + LLM 判定的两步走**：Graphiti 把 LLM 矛盾比对限制在「同一实体对之间的语义相关边」，防跨实体误配且大幅降计算量。兰台 deprecate/冲突检测应先做结构化召回（同 subject+relation、或 `valid_from/valid_to` 区间近邻的候选对），再由 LLM 判「是否矛盾 + 是否时间重叠」，可减少盲目 deprecate 与 LLM 成本。
4. **检索上下文显式注入时间窗口**：Zep 的 constructor 把每条 fact 的 `(valid_at, invalid_at)` 拼成「FACT (Date range: from - to)」交给生成端。兰台检索时应在上下文模板中携带 `valid_from/valid_to`（尤其 temporal 类查询与 Chronos 过期检测场景），而不是只返回文本块。
5. **时序抽取 prompt 纪律（附录 6.1.5 可直接复用）**：ISO 8601 完整格式（YYYY-MM-DDTHH:MM:SS.SSSSSSZ）、以 reference timestamp 为基准计算相对时间、只为「明确建立或改变关系」的日期设值、不从相关事件推断日期、缺时间默认 00:00:00、缺日期默认 1 月 1 日、默认 Z/UTC。这套约束可直接收进兰台反思/抽取 prompt，降低 `valid_from/valid_to` 噪声与冲突误报率。
6. **增量更新 + 周期全量重算 + token/延迟度量**：社区用标签传播单步动态扩展（低延迟、低 LLM 成本）但周期刷新防漂移——对应兰台的摘要/反思节点可做「增量维护 + 定期重算」两段式；同时把 Zep 的量化结果（115k→1.6k tokens、28.9s→2.58s、-90% 延迟）作为参照，为兰台检索引入「上下文 token 预算」与延迟指标，作为检索质量与成本代理。

**引用注记**：本笔记所有机制与数字均出自已保存的全文 `docs/research/papers/06-zep-fulltext.md`（源自 arXiv HTML v1）；个别「原文如此」处已在正文标注，未做任何编造或外推。
