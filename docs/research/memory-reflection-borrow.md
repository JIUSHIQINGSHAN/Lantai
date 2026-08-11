# 兰台记忆（Lantai）反思/蒸馏模块可借鉴机制调研报告

> 调研日期：2026-08-11
> 调研方法：逐项目检索一手来源（arXiv 论文/HTML 全文、官方 GitHub 仓库 README/源码、官方文档、OpenAI 官方博客），对照兰台现有 gate → proposal（add/update/merge/deprecate）→ pending_review（锦囊）→ checkpoint 回滚 → Ebbinghaus 遗忘链路（见 CONTEXT.md、ADR-0005/0009/0010）评估可移植性。
> 约束遵守：只采用一手来源，未采用任何二手转述文；每条事实后标注来源 URL；检索不到或未公开的内容一律标注「未确认」，不推测、不编造。

---

## 一、摘要（直接回答「哪些机制值得抄」）

1. **反射触发应优先「信息量驱动」而非「固定周期」**：Generative Agents 用「新记忆重要性累加值降到阈值（默认 150）以下才反射」的计数器（官方源码 scratch.py 的 importance_trigger_max），LangMem 用「发现旧记忆错误/过时」作为自编辑触发条件（官方 API 文档），Letta dreaming 用「N 条用户消息后 / 上下文压缩时」双触发（官方文档）。兰台已有 importance/novelty 评分与 decay_score，可把 7 天周期升级为「累计重要性水位 + 上下文压力」混合触发。
2. **蒸馏输出应当「提案化」而非「直接落库」**：Mem0 的 ADD/UPDATE/DELETE/NOOP 四类操作（官方源码 prompts.py）、LangMem 工具化的 create/update/delete（官方 API 文档）、agentmem 的 hypothesis→active→validated→deprecated→superseded 信任生命周期（官方 README）——三者产出物都与兰台 proposal 枚举（add/update/merge/deprecate）天然同构，反思结果可直接映射为 proposal 走既有 gate/pending_review 链路。
3. **蒸馏要有「双角色闸门」：curator 提案 + rejecter 复核 + 置信度门槛**：Cognee 的 session distillation 明确写出「curator 提出教训、writer/rejecter 对照既有教训与图实体校验、仅当未被标 harmful 且置信度通过门槛才接受」（官方 docs improve.md）。这与兰台「宁 miss 不脏写」的锦囊待审策略是同构实现，是本次调研中与兰台哲学最一致的现成机制。
4. **反思记忆应带证据指针、可追溯可回滚**：Generative Agents 每条 insight 记录 evidence node ids（官方源码 reflect.py），A-MEM 的 memory evolution 会在新记忆融入时更新旧记忆的上下文/关键词/标签（arXiv 全文），agentmem 用 source_hash 追踪来源文件漂移并在段落删除时自动 deprecate（官方 README）。这对应兰台 checkpoint 回滚与 conflict_event 审计账本的延伸。
5. **「验证成功才沉淀」是 procedural/rule 记忆蒸馏的正确门槛**：Voyager 只有 self-verification 确认任务完成才把程序提交进技能库，失败最多迭代 4 轮即放弃换任务（arXiv 2.3 节）；OpenAI Dreaming 把「时间流逝导致记忆过期」（如「下周六生日」在周日过后失效）作为核心目标之一（官方博客）。兰台 rule lane 蒸馏可套用「验证通过才入库」，并给 fact lane 加过期判定。
6. **记忆健康审计应作为反思的输入选择器与事后验证**：agentmem 的 conflict/staleness/health_score（0-100）与 Letta 的 /doctor（审计放置、重复、token 占用）都提供了「挑出该反思什么、反思后系统是否变健康」的可度量闭环（官方 README / 官方文档），兰台可复用于反思任务的选取与效果评估。

---

## 二、逐项目深挖

### 1. Generative Agents（Park et al., UIST 2023，研究基线）

- **触发策略**：反射由「重要性累加计数器」驱动：新事件/思想写入时按重要性（poignancy，LLM 按 1-10 打分）扣减 importance_trigger_curr，降到 ≤0 且存在事件/思想时触发一次反射，随后重置计数器（官方源码 reflect.py 的 reflection_trigger/reset_reflection_counter；默认阈值 importance_trigger_max = 150，见 memory_structures/scratch.py）。此外对话结束时会对整段对话生成 planning thought 与 memo thought（官方源码 reflect.py 的 reflect）。并非固定周期触发。
- **输入选择**：generate_focal_points 取最近 importance_ele_n 条事件/思想（排除 idle），让 LLM 生成 3 个 focal points（「最显著的高层问题」）；再对每个 focal point 用记忆检索（recency/importance/relevance 加权）拉取相关节点，对每组节点生成 5 条 insights（官方源码 reflect.py）。
- **处理过程**：两段式 LLM 加工：先问「what are the 3 most salient high-level questions」，再基于检索出的相关记忆生成洞察；每条 insight 记录其证据节点（evidence node ids）；以 S-P-O 三元组 + keywords + embedding 入库，并打分 poignancy（1-10）（官方源码 reflect.py）。
- **输出形态**：反思作为 thought 类型写入 memory stream，与普通事件同等参与检索（检索权重 recency/relevance/importance = 1/1/1，recency_decay=0.99；源码 scratch.py）；反思带 30 天 expiration；不做删除/合并，只靠检索排序优胜劣汰（论文 arXiv:2304.03442）。
- **审查/安全**：无人工闸门，自主写入；无去重、无回滚（CSV 级存储）；反思本身也参与遗忘（expiration）。论文消融显示 reflection 组件对可信行为有显著贡献（arXiv:2304.03442）。
- **对兰台的启示**：「重要性累加触发器」是比固定 7 天更符合信息量的调度；evidence node ids 是可审计性最小实现——兰台可让反思候选携带 source memory_id 数组，天然接入 conflict_event 账本与 checkpoint。

### 2. Reflexion（Shinn et al., 2023）

- **触发策略**：失败驱动——环境把 binary/scalar 反馈转成自然语言后，在下一 episode 注入反思文本；反思存在 episodic memory buffer，跨 episode 复用（论文摘要，arXiv:2303.11366）。
- **输入选择**：任务失败的反馈信号，三种来源：简单环境反馈、针对常见失败的预定义启发式、自评估（决策任务用 LLM 二分判断、编程任务用自写单元测试）（论文摘要/正文，arXiv:2303.11366）。
- **处理过程**：LLM 把评估信号放大为自然语言经验摘要（verbal reinforcement，「语义梯度」），作为下一轮尝试的额外上下文；不更新权重（论文摘要，arXiv:2303.11366）。
- **输出形态**：反思文本进入 episodic memory buffer（短期，episode 内）与 long-term memory（跨 episode 的 actor-critic 设置）；不产生结构化记忆变更（论文正文，arXiv:2303.11366）。
- **审查/安全**：完全自主，无闸门；论文自陈依赖 LLM 自评估能力、无成功保证（论文摘要，arXiv:2303.11366）。
- **对兰台的启示**：把「失败」作为反思触发器、把失败反馈（错误信息/批评）作为反思输入——对应兰台低置信提取、gate 拒绝、conflict 未决场景；但 Reflexion 是 prompt 层技术，不产结构化记忆，兰台应取其触发语义、不取其存储形态。

### 3. LangMem（LangChain 官方）

- **触发策略**：两条路径。hot path：agent 自主调用 create_manage_memory_tool，默认 instructions 列出四类触发：「发现新用户偏好」「收到显式记住请求」「工作中记录重要上下文」「发现既有记忆错误/过时」（官方 API 文档 reference/tools）。background：create_memory_store_manager 在对话后异步提取/整合，可延迟防抖（官方文档 background_quickstart / guides）；「月度/周度固定周期」未在官方文档中找到，标未确认。
- **输入选择**：对话消息 + 检索到的相关既有记忆（query_limit 默认 5 条，官方 API 文档 reference/memory）；支持 Pydantic schema 限定抽取结构（Profile 单文档 upsert vs Collection 多文档追加，官方文档 conceptual_guide）。
- **处理过程**：LLM 对「对话 + 既有记忆」做 enrichment：自动搜索相关记忆、抽取新信息、更新既有记忆、维护版本历史（官方 API 文档 reference/memory 的 create_memory_store_manager）；create_memory_manager 显式暴露 enable_inserts / enable_updates / enable_deletes 三开关（官方 API 文档 reference/memory）。
- **输出形态**：新增/更新/删除记忆；Profile 形态 = 单文档最新状态（避免多余记忆），Collection 形态 = 可检索的多条记忆（官方文档 conceptual_guide）。
- **审查/安全**：无人工闸门；但删除默认关闭（enable_deletes=False）、插入更新默认开启（官方 API 文档 reference/memory）——「保守写」的默认值设计；记忆版本历史可回溯（官方 API 文档 reference/memory）。
- **对兰台的启示**：自编辑工具的四类触发条件（尤其「发现旧记忆错误/过时」）可直接翻译成兰台反射模块的扫描条件；删除默认关闭与兰台「宁 miss 不脏写」一致；Profile 单文档 upsert 值得作为 preference lane 蒸馏形态参考。

### 4. Mem0

- **触发策略**：每次 memory.add(messages) 即触发「抽取 + 更新」流水线（官方 README 的 chat loop 示例），无独立后台反思周期。
- **输入选择**：对话（user+assistant 消息）+ 检索到的相关既有记忆（向量 + BM25 + 实体增强的混合检索，官方 README / docs）。
- **处理过程**：两段式：FACT_RETRIEVAL_PROMPT 从对话抽取事实（官方源码 configs/prompts.py）→ MEMORY_UPDATE_PROMPT 让 LLM 对每条既有记忆判定操作，四类：ADD（新信息）、UPDATE（新事实细化/修正旧记忆，带 old_memory 字段）、DELETE（矛盾/被指示删除）、NONE（已存在无变化）（官方源码 configs/prompts.py 的 few-shot 示例）。变更写入 SQLite history store（官方 docs open-source/overview）。
- **输出形态**：记忆库的增/改/删；无独立「反思记忆」类型。
- **审查/安全**：LLM 判定即权威，直接落库，无置信度门槛、无人工闸门；可回溯依赖 history store（官方 docs open-source/overview）。
- **对兰台的启示**：ADD/UPDATE/DELETE/NOOP 分类与 few-shot 模板是兰台 proposal 枚举（add/update/merge/deprecate）现成的 LLM 决策 prompt 基础；但 Mem0 缺人工闸门，兰台必须保留 pending_review——这恰是兰台差异化所在。

### 5. Letta / MemGPT（Packer et al. 2023 + 现 Letta 产品）

- **触发策略**：论文版 MemGPT：上下文压力触发——队列达 warning token 数（如 70% 上下文）时插入「memory pressure」系统警告，LLM 自行决定把重要信息写入 working/archival memory；达 flush 阈值（100%）时强制 flush 并生成递归摘要（论文 2.2 节，arXiv:2310.08560）。现 Letta：dreaming 用后台 subagent 复盘，可在配置的消息条数后或上下文被压缩时运行（官方文档 configuration/memory）；另有显式 /remember、/sleeptime、/doctor 与「reorganize memory」命令（官方文档 configuration/memory）。
- **输入选择**：论文版：FIFO 队列中的会话历史与告警；现 Letta dreaming：近期对话 + 既有记忆文件（MemFS 的 system/ 目录常驻系统提示，其余按需读取，官方文档 concepts/memfs）。
- **处理过程**：论文版：LLM 通过函数调用自主编辑记忆（self-directed editing），函数 schema 写在系统指令中（论文 2.3 节，arXiv:2310.08560）。现 Letta：后台 subagent 复盘对话、提炼经验、提交到 MemFS（git 版控的 markdown 记忆文件系统）；reorganize 工作流先备份再拆分大文件、合并重复、重组层级（官方文档 configuration/memory）；/doctor 审计放置/重复/系统提示 token 用量（官方文档 configuration/memory）。
- **输出形态**：记忆分层——in-context core memory（system/ 目录，每轮加载）vs archival/reference（按需检索）；对话外历史进 recall storage（官方文档 concepts/memfs；论文 2.1 节）。输出为 markdown 文件 + git 提交。
- **审查/安全**：无人工闸门，agent 自主提交；但每次编辑都是 git commit，天然版本化/可回滚/可 diff（官方文档 concepts/memfs）；记忆文件人类可读可改（官方文档 configuration/memory）。
- **对兰台的启示**：dreaming 的「消息数 + 上下文压力」双触发是兰台 autodream 的直接参考；git 版控输出 ≈ 兰台 checkpoint 回滚的同类思路；/doctor 审计重复/放置/占用 = 记忆健康检查的工程化样板。

### 6. AgentMem（按任务清单指定为 ICLR 2025 论文）

- **核实结论（先说实话）**：名为「AgentMem」的 ICLR 2025 论文无法在一手来源库定位——arXiv 全字段检索「AgentMem」返回 0 结果（https://arxiv.org/search/?searchtype=all&query=AgentMem）；OpenReview ICLR 2025 检索与论文索引库亦未命中。据此标「未确认」。机制上最接近「元记忆审查与清理」描述、且可一手验证的实体有两个：
  - **(a) Thezenmonster/agentmem（GitHub 开源 MCP 项目，官方 README）**：为编码 agent 设计的「治理型记忆」，核心是信任生命周期 hypothesis → active → validated → deprecated → superseded；检索按 validated > active > hypothesis 排序，deprecated/superseded 自动排除；内置冲突检测（Jaccard 主题重叠 + 句级否定匹配，duplicates 与 contradictions 分开，严重度 critical/warning 分级）、陈旧检测（N 天未更新 / 源文件丢失 / source_hash 漂移）、健康分 health_score 0-100（按冲突数、陈旧占比、孤儿引用、deprecated 权重等）；来源同步：同 hash 跳过、异 hash 更新、段落删除→deprecate、段落恢复→resurrect（官方 README，https://github.com/Thezenmonster/agentmem）。
  - **(b) A-MEM（Agentic Memory，arXiv:2502.12110，NeurIPS 2025 poster / 原 ICLR 2025 投稿，OpenReview FiM0M8gcct）**：Zettelkasten 式记忆网络——新记忆生成含 context/keywords/tags 的原子 note → 语义检索历史 → Link Generation 建链 → Memory Evolution：对近邻记忆决定是否更新其上下文/关键词/标签（arXiv 3.3 节与消融 4.4 节；OpenReview https://openreview.net/forum?id=FiM0M8gcct）。
- **审查/安全**：agentmem：无人工闸门，但状态机 + 冲突/陈旧报告提供「事后人工可审」的元数据；A-MEM：自主演化，无闸门（arXiv 全文）。
- **对兰台的启示**：agentmem 的信任状态机 = 兰台 candidate/proposal 状态语义的再简化；conflict/staleness 检测可作为反思「输入选择器」（挑冲突与陈旧记忆进反思）并复用兰台 conflict_event 账本；A-MEM 的 Memory Evolution 则是 merge 提案的自动化近似——兰台可做成「提出 merge 提案」而非直接改写旧记忆，保持人工裁决。

### 7. Voyager（Wang et al., 2023）

- **触发策略**：自动课程（automatic curriculum）持续提出新任务；任务失败时进入「迭代提示」改进循环，同一任务最多 4 轮代码生成仍失败则放弃并换任务（论文 2.1/2.3 节，arXiv:2305.16291）。
- **输入选择**：当前任务 + 从技能库检索的 top-5 相关技能（技能描述 embedding 检索，GPT-3.5 生成的解决方案建议 + 环境反馈拼成查询） + 上轮代码 + 环境反馈 + 执行错误 + 批评（论文 2.2/2.3 节，arXiv:2305.16291）。
- **处理过程**：三类反馈迭代改进程序：环境反馈（执行中间进度）、执行错误（解释器报错）、self-verification（独立 GPT-4 批评者判断任务是否完成，失败时给改进建议）（论文 2.3 节，arXiv:2305.16291）。
- **输出形态**：procedural memory——技能库：可执行代码 + GPT-3.5 生成的自然语言描述，以描述 embedding 为键存入向量库；只增不删、可组合（论文 2.2 节，arXiv:2305.16291）。
- **审查/安全**：self-verification 通过才入库（无人工）；无去重/删除/过期机制，靠检索取 top-k；「技能改进」以迭代精修新程序实现，论文未描述对既有技能做版本替换的机制（标未确认）。
- **对兰台的启示**：rule lane 蒸馏的模板——「验证成功才沉淀、失败反馈进上下文、最多 N 轮即放弃」；兰台可把「自我验证」作为 rule 提案进入 pending_review 前的预筛条件，把低置信失败送入待审而非自动丢弃。

### 8. Zep / Graphiti（Zep AI，含 arXiv:2501.13956）

- **触发策略**：每次摄入新 episode（对话/业务数据）即增量建图，无独立反思/蒸馏周期（官方 README）。
- **输入选择**：N/A（无反思步骤）——其「更新」发生在摄入时的图更新，LLM 抽取实体/关系/事实三元组（官方 README + 论文摘要，arXiv:2501.13956）。
- **处理过程**：时序知识图谱：事实边带有效性窗口（valid_at/invalid_at），信息变化时旧事实自动 invalidate 而非删除，保留完整时间史；每条派生事实可追溯回 episode（provenance）；支持 prescribed（Pydantic 定义）与 learned ontology；混合检索 = 语义 embedding + BM25 + 图遍历，无 LLM 摘要式加工（官方 README）。
- **输出形态**：实体（随时间演化的 summary）、事实/关系边（时间有效窗口）、episode（原始数据流）；「失效」是核心更新语义（官方 README）。
- **审查/安全**：图更新由 LLM 抽取驱动，无人工闸门；失效而非删除 = 天然可回滚历史（官方 README）。
- **对兰台的启示**：Graphiti 没有传统意义的 reflection 模块，但「事实边 invalidate-not-delete + 双时间轴」与兰台 deprecate 提案 + Chronos 时间轴语义一致，验证了兰台「deprecate 而非物理删除」路线的正确性；其自动失效可启发兰台为 conflict 命中提供「自动 deprecate 提案」。

### 9. OpenAI ChatGPT「Dreaming」（官方博客）

- **触发策略**：后台进程——2025-04 引入 v0：ChatGPT 在后台参照聊天历史自动策展记忆；2026-06 发布更强大的新架构（Dreaming V3），计算开销约降 5 倍并开始向 Free 用户开放。确切调度周期/条件未公开，标未确认（官方博客 https://openai.com/index/chatgpt-memory-dreaming/）。
- **输入选择**：跨多会话的聊天历史 + 既有记忆状态；目标是「从多段对话学习并合成记忆状态」（官方博客）。
- **处理过程**（已公开部分）：自动 curate/合成，明确针对三类问题：staleness（记忆随时间过期，如「下周六生日」在周日过后应失效）、correctness、scalability；官方评估围绕三个目标：carry forward context（延续上下文）、follow preferences and constraints（遵守偏好与约束）、stay current over time（随时间的时效性）（官方博客）。内部算法（聚类、置信度、去重细节）未公开，标未确认。
- **输出形态**：合成/更新后的记忆 + 可审阅的 memory summary 页；用户可增改信息、纠正或忽略具体条目（官方博客）。
- **审查/安全**：用户侧可见、可纠正、可忽略（summary 页 + 设置项）；系统侧机制未公开（官方博客 + Memory FAQ 链接 https://help.openai.com/en/articles/8590148-memory-faq）。
- **对兰台的启示**：Dreaming 证明「后台周期记忆合成」是 2026 主战场（与 direction-research-report.md 结论一致），其「时间流逝使记忆过期」的显式目标对应兰台 Ebbinghaus/expiration 的强化方向；memory summary 可审阅页 = 锦囊待审队列的消费端 UI 形态。注意：Dreaming 机制细节不透明，兰台可借鉴的是目标函数（freshness/correctness/scalability）而非算法。

### 10. Cognee（topoteretes/cognee，官方文档）

- **触发策略**：session 结束/后台自改进——remember(data, session_id=...) 写 session 缓存（快、原始），默认 self_improvement=True 时后台触发一次 Improve pass；也可显式调用 cognee.improve(dataset=..., session_ids=[...])（官方 docs sessions-and-caching / improve）。核心操作集为 remember / recall / improve / forget（官方 README）。
- **输入选择**：session 缓存内容（对话 Q&A、agent 工具轨迹 traces、session context guidance）+ 既有知识图谱；Improve 可选取「门控的 session guidance」蒸馏（官方 docs improve.md）。
- **处理过程**（improve() 多阶段，官方 docs improve.md）：① feedback weights——按 session 问答评分对参与检索的图节点/边升降 feedback_weight（好评升权、差评降权）；② 持久化 session Q&A 与 agent traces 进图；③ 提取 session context 教训；④ 蒸馏（distill）：curator 提出持久教训，writer/rejecter 对照既有 lessons 与图实体校验，只有「未被评 harmful 且置信度通过蒸馏门槛」的教训才被接受，落为 session_learnings（entity-anchored 教训文档）；⑤ 可选 truth-subspace 锚点；⑥ enrichment + 可选 global context index（dataset 级 bucket/root 摘要）；⑦ 新图关系回写 session 缓存。
- **输出形态**：蒸馏后的 lessons 文档（session_learnings）、图 enrichment（新三元组/摘要）、global context 摘要层；「记忆作为主动自改进层」由 improve 承载（官方 README 定位语「Persistent and Learning Agents」）。
- **审查/安全**：最接近兰台哲学的机制——蒸馏闸门 = 有害过滤 + 置信度门槛 + rejecter 复核；feedback 只调权重不直接改内容；session 缓存默认 7 天 TTL（官方 docs sessions-and-caching）。
- **对兰台的启示**：curator/rejecter 双角色 + 置信度门槛 = 「宁 miss 不脏写」的工程实现，建议整体搬入蒸馏模块：反思产物先成候选 → 过蒸馏闸门 → 进 pending_review；用户裁决结果回流为 feedback_weight（对应兰台 proposal 拒绝/采纳的反馈回路）。注：Cognee 文档未再使用「ECL」缩写（现为 add/cognify/improve + remember/recall/improve/forget 操作面），「Extract-Cognify-Load」命名是否仍官方使用标未确认。

### 11. A-MEM（Agentic Memory）—— 已在 §6(b) 覆盖，不重复展开

### 12. 其他值得注意的项目

- **Cline Memory Bank（官方文档 docs.cline.bot/best-practices/memory-bank）**：结构化 markdown 记忆层级（projectbrief / productContext / activeContext / systemPatterns / techContext / progress，核心文件每次任务必读，可选文件渐进披露）；更新触发 = 发现新模式 / 重大变更后 / 显式「update memory bank」命令 / 上下文需澄清；activeContext.md 更新最频繁。机制上是「agent 写入纪律 + 显式快照」，无 LLM 后台蒸馏，但「核心常驻 + 参考渐进披露」的分级思想与兰台 working/long_term 分层、Letta system/ 目录同构，可借鉴。
- **memobase（GitHub，memodb-io/memobase）**：用户 profile（多槽位，LLM 抽取/更新）+ event 记忆（embedding 检索），server 以 config.yaml 配置 profile 槽位（官方 src/server/readme.md）。未确认其存在独立后台反思/蒸馏机制（README 未描述）；其「profile 槽位 + 事件流」双形态值得参考，但无独特反思机制。
- **Basic Memory（GitHub，basicmachines-co/basic-memory）**：本地优先 Markdown 知识库 + 观察/wikilink 知识图 + 语义检索（可选 rerank）+ MCP 工具（官方 README）。未确认存在后台反思机制（README 未描述）；其价值在可读可导出、human/agent 双写，与兰台「可读可导出」路线一致，但无反思机制可抄。
- **MCMA（arXiv:2601.07470，2026-01）**：元认知视角——把「记忆如何结构化/抽象/复用」当成可学习技能：冻结任务模型 + DPO 训练的 memory copilot 决定记忆抽象与选择；抽象层级按任务相似度选择（论文摘要）。这是训练型方案而非 prompt 型，兰台不宜直接采用，但其「记忆管理作为一等认知技能、分层抽象按需选择」的视角可启发兰台 reflection 的分层设计（事实 → 规则 → 经验摘要）。

---

## 三、横向对比表（项目 × 触发 / 输入 / 过程 / 输出 / 审查）

| 项目 | 触发 | 输入 | 处理过程 | 输出 | 审查/安全 |
|---|---|---|---|---|---|
| Generative Agents | 新记忆重要性累加 ≤ 阈值（默认 150） | 最近高重要性事件 + 检索相关记忆 | LLM 两段式：3 焦点问题 → 5 洞察 + 证据指针 + 1-10 重要性 | 反思 thought（带证据、30 天过期）入记忆流 | 无闸门，自主写入；无去重/回滚 |
| Reflexion | 任务失败 | 失败反馈（env/启发式/自评估） | 反馈转自然语言经验摘要，注入下轮 | 反思文本进 episodic buffer | 无闸门；自评估无保证 |
| LangMem | agent 自主调用（4 类指令触发）+ 后台异步 | 对话 + 相关既有记忆（top-5） | LLM enrichment：插/更/删 + 版本历史 | 增/改/删记忆；profile 单文档 | 无人工闸门；删除默认关闭；版本可回溯 |
| Mem0 | 每次 add() 同步 | 对话 + 混合检索既有记忆 | 抽事实 → LLM 判 ADD/UPDATE/DELETE/NONE | 记忆库增改删 | LLM 即权威；history 可回溯；无闸门 |
| Letta/MemGPT | 上下文压力（70% 警告/100% flush）+ 消息数/压缩触发 dreaming | 近期对话 + 既有记忆文件 | 函数自编辑；后台 subagent 复盘；reorganize/doctor | MemFS markdown + git 提交 | git 版本化可回滚；无人工闸门 |
| agentmem（Thezenmonster） | 写入时治理 + 显式 promote/deprecate/supersede | 全库（conflict/staleness 扫描） | 状态机 + 冲突/陈旧/健康评分 + 来源 hash 同步 | 状态迁移 + 冲突/陈旧报告 | 状态排序召回；deprecated/superseded 排除；无人工闸门 |
| A-MEM | 每条新记忆写入时 | 新记忆 + 语义近邻历史 | 建链 + Memory Evolution（更新旧记忆属性） | 原子 note 网络，链接演化 | 无闸门，自主演化 |
| Voyager | 课程新任务；失败迭代 ≤4 轮 | 任务 + top-5 技能 + 三反馈 | 迭代精修代码，self-verification 成功才提交 | 技能库（代码 + 描述 embedding） | 自验证即入库；只增不删 |
| Zep/Graphiti | 每次 episode 摄入 | 对话/业务数据 | LLM 建时序图；变更时旧边自动 invalidate | 实体/事实边（时间窗口）+ episode | 失效非删除；无人工闸门 |
| ChatGPT Dreaming | 后台进程（周期未公开） | 多会话历史 + 既有记忆 | 自动合成/策展，目标 freshness/correctness/scalability | 合成记忆 + 可审阅摘要页 | 用户可纠正/忽略；内部机制未公开 |
| Cognee | session 结束/后台 improve（默认开） | session Q&A + traces + 门控 guidance | feedback 权重 → 持久化 → curator/rejecter 蒸馏（harm+置信度门槛）→ enrichment | session_learnings + 图 enrichment + 摘要层 | 蒸馏闸门 + 有害过滤；反馈只调权 |
| Cline Memory Bank | 新模式/重大变更/显式命令/需澄清 | 项目上下文 | agent 写 markdown 层级文档 | 6 类核心文件 + 可选文件 | 人类可审；无 LLM 闸门 |

---

## 四、可直接借鉴 TOP 5 机制（按对兰台价值排序）

**1. 重要性累加触发器（Generative Agents）——把固定 7 天周期换成「信息量水位」**
把反射周期从「autodream 每 7 天」改为：新记忆重要性（兰台已有 gate 置信度/新颖度评分）累加超过阈值才触发蒸馏（官方源码阈值默认 150，arXiv:2304.03442 代码仓库）。融入方式：gate 已产出置信度/新颖度分数 → 在 memory 表加 importance_pool 累加字段 → worker 巡检时若 importance_pool ≥ threshold 则进入反思批次；可叠加 Letta 的「上下文压力」触发（Letta 官方文档）作为补充信号。优点：预算与信息量成正比，避免固定周期空转或漏蒸。

**2. 蒸馏双角色闸门 curator + rejecter + 置信度门槛（Cognee）——兰台「宁 miss 不脏写」的工程化实现**
反思产物不直接落库：curator（LLM）提出蒸馏候选 → rejecter（LLM）对照既有记忆/规则校验（重复、矛盾、有害、置信度）→ 只有通过者进 pending_review（官方 docs improve.md 的 distill 阶段）。融入方式：反思输出直接构造为 proposal（add/update/merge/deprecate）→ 复用 gate 五档决策与锦囊待审队列 → 用户裁决。这与兰台既有哲学完全同构，是本次调研最值得整体搬入的机制。

**3. 操作分类与信任生命周期：ADD/UPDATE/DELETE/NOOP + create/update/delete + supersede/deprecate（Mem0 / LangMem / agentmem）**
反思 LLM 的产出格式直接用「操作 + 目标记忆 + 新旧文本」结构化：Mem0 的 four-operation few-shot（官方源码 prompts.py）、LangMem 工具化的 create/update/delete 与「删除默认关闭」（官方 API 文档）、agentmem 的 supersede(old, new) 与 deprecate(reason)（官方 README）。融入方式：反思模块的 prompt 输出 schema = proposal 枚举 + evidence 数组 + 置信度，直接对接 GET /candidates/pending 裁决入口与 checkpoint 回滚；supersede 语义对应兰台 deprecate + 新 add 的组合提案。

**4. 带证据指针 + 演化的反思记忆（Generative Agents evidence node ids / A-MEM Memory Evolution）**
每条反思/蒸馏产物必须携带 source memory_id 数组（官方源码 reflect.py 的 evidence），并允许新记忆触发对旧记忆的「演化更新」候选（A-MEM 的 Memory Evolution，arXiv:2502.12110）。融入方式：proposal 增加 evidence_ids 字段 → 天然写入 conflict_event 账本（ADR-0010）；「演化更新」只产生 merge/update 提案而非直接改写，保持人工裁决 + checkpoint 可回滚；同时为「谁支撑了这条反思」提供审计链。

**5. 记忆健康审计与陈旧检测（agentmem health/conflict/stale + Letta /doctor）**
把反思输入选择从「全量扫描」改为「问题驱动」：冲突检测（主题重叠 + 否定匹配，critical/warning 分级）、陈旧检测（N 天未更新 / source_hash 漂移 / 源文件缺失）、健康分 0-100（官方 README；Letta /doctor 审计重复与 token 占用，官方文档）。融入方式：新增后台巡检，产出「健康报告 + 待反思候选清单」→ 只把冲突/陈旧/低健康项送入反思批次 → 反思后再次跑健康分作为蒸馏效果自证（呼应 dry-run/参数矩阵的自证打法）；复用兰台既有 conflict_event 账本，不新增存储。

**补充观察（不占 TOP 5，但值得立项时参考）**：Voyager「self-verification 通过才入库、失败 ≤4 轮放弃」适用于 rule lane（arXiv:2305.16291 2.3 节）；OpenAI Dreaming 把「时间流逝使记忆过期」列为显式目标（官方博客）对应兰台 expiration/Ebbinghaus 增强；Cline Memory Bank 的「核心常驻 + 参考渐进披露」对应兰台 working/long_term 分层消费。

---

## 五、来源清单（检索日期 2026-08-11）

1. Park et al., Generative Agents: Interactive Simulacra of Human Behavior（UIST 2023）— https://arxiv.org/abs/2304.03442 ；官方代码 https://github.com/joonspk-research/generative_agents （反射实现 reverie/backend_server/persona/cognitive_modules/reflect.py、参数 memory_structures/scratch.py）
2. Shinn et al., Reflexion: Language Agents with Verbal Reinforcement Learning — https://arxiv.org/abs/2303.11366
3. LangMem 官方文档（Introduction / Background Quickstart / Core Concepts / Memory Tools Guide / API Reference）— https://langchain-ai.github.io/langmem/ （reference/tools、reference/memory）
4. Mem0 官方仓库与文档 — https://github.com/mem0ai/mem0 （mem0/configs/prompts.py 操作分类与 few-shot）、https://docs.mem0.ai/（llms.txt、open-source/overview）
5. MemGPT 论文（Packer et al. 2023）— https://arxiv.org/abs/2310.08560
6. Letta 官方文档（Memory & dreaming / MemFS / Subagents）— https://docs.letta.com/configuration/memory/index.md 、https://docs.letta.com/concepts/memfs/index.md 、https://docs.letta.com/llms.txt
7. Thezenmonster/agentmem 官方 README（信任生命周期/冲突/陈旧/健康分/来源同步）— https://github.com/Thezenmonster/agentmem
8. A-MEM: Agentic Memory for LLM Agents — https://arxiv.org/abs/2502.12110 ；OpenReview https://openreview.net/forum?id=FiM0M8gcct
9. Voyager: An Open-Ended Embodied Agent with LLMs — https://arxiv.org/abs/2305.16291 （2.1-2.3 节）
10. Zep: A Temporal Knowledge Graph Architecture for Agent Memory — https://arxiv.org/abs/2501.13956 ；Graphiti 官方 README https://github.com/getzep/graphiti
11. OpenAI, Dreaming: Better memory for a more helpful ChatGPT（2026-06-04）— https://openai.com/index/chatgpt-memory-dreaming/ ；Memory FAQ https://help.openai.com/en/articles/8590148-memory-faq
12. Cognee 官方 README 与文档（improve / sessions-and-caching / pipelines）— https://github.com/topoteretes/cognee 、https://docs.cognee.ai/core-concepts/main-operations/improve.md 、https://docs.cognee.ai/core-concepts/sessions-and-caching.md
13. Cline Memory Bank 官方文档 — https://docs.cline.bot/best-practices/memory-bank
14. memobase 官方仓库 — https://github.com/memodb-io/memobase （src/server/readme.md）
15. Basic Memory 官方仓库 — https://github.com/basicmachines-co/basic-memory
16. MCMA: Learning How to Remember（2026）— https://arxiv.org/abs/2601.07470

---

## 六、未确认项（诚实标注）

- 「AgentMem（ICLR 2025 论文）」无法定位：arXiv 全字段检索、OpenReview 检索、论文索引库均无结果；任务描述中的「元记忆审查与清理」机制实为 Thezenmonster/agentmem（GitHub 项目，非论文）与 A-MEM（Agentic Memory 论文）两个不同实体，报告中已如实覆盖并注明差异。
- ChatGPT Dreaming 内部机制：后台合成的确切调度周期、聚类/置信度/去重算法、删除策略均未公开（官方博客只公布目标函数与用户侧能力）。
- LangMem「月度/周度记忆收集」：官方文档只描述 hot-path 工具与后台 store manager（可延迟防抖），未找到固定月度/周度调度说明。
- memobase / Basic Memory 的反思机制：两者官方 README 均未描述后台反思/蒸馏模块（未确认存在）。
- Voyager 技能库的更新/去重：论文确认技能「只增、可组合、检索 top-5」，但未描述对既有技能做版本替换/去重的机制。
- Cognee「ECL（Extract-Cognify-Load）」命名：当前官方文档操作面为 add/cognify/improve 与 remember/recall/improve/forget，「ECL」缩写是否仍为官方术语未确认（本报告按现行文档描述管线）。
- 各 GitHub 仓库的 star 数、近期版本号随日期漂移，本报告不引用数字快照。
