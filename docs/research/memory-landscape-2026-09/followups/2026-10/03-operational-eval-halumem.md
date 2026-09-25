# 03 · 操作级记忆评测方法论（HaluMem + MemoryAgentBench）

调研日期：2026-09-25。目标：抽取「阶段/操作级指标」定义方式，映射到兰台「提取→闸门→入库→索引→召回→注入」六阶段管线。

## 一、HaluMem（arXiv:2511.03506）

- 论文：*HaluMem: Evaluating Hallucinations in Memory Systems of Agents*；v1 2025-11-05，v3 2026-01-05。自述为「the first operation level hallucination evaluation benchmark tailored to memory systems」：不再只做端到端 QA（难以定位错误发生在哪个操作阶段），而是在**存储与检索的各操作阶段**分别计量幻觉（制造、错误、冲突、遗漏四类）。来源：https://arxiv.org/abs/2511.03506（检索 2026-09-25）
- 三个操作级任务与指标（公式取自 HTML 全文 v3，检索 2026-09-25：https://arxiv.org/html/2511.03506v3）：
  1. **记忆抽取**：Memory Recall = N_correct/N_should（另有按重要度加权的 Weighted Recall，部分分 s_i∈{1,0.5,0}）；Memory Accuracy = Σs_j/N_extract（抽取侧反幻觉）；Target Memory Precision（对齐参考记忆后计精确率）；False Memory Resistance = N_miss/N_D（对「AI 提及但用户未确认」的干扰内容的抵抗）；Memory Extraction F1 = 2·R·P/(R+P)。
  2. **记忆更新**：Updating Accuracy = N_correct-upd/N_target-upd；Updating **Hallucination Rate** = N_wrong-upd/N_target-upd；Updating **Omission Rate** = N_missed-upd/N_target-upd。
  3. **记忆问答**（端到端，覆盖抽取+更新+检索+生成）：QA Accuracy / QA Hallucination Rate / QA Omission Rate。
- 数据构造（同 HTML v3）：六阶段管线——Persona 构造（Persona Hub 采样 + GPT-4o 精炼）→ Life Skeleton → Event Flow → 会话摘要与记忆点 → 会话生成（**对抗内容注入**：制造「False but similar memories」，多轮对话 + 记忆自校验）→ 问题生成（六类：Basic Fact Recall、Multi-hop、Dynamic Update、**Memory Boundary**（考系统是否会编造）、Generalization、**Memory Conflict**（前提与已知记忆直接矛盾））。HaluMem-Medium（20 用户、30,073 轮、约 160k token、14,948 记忆点、3,467 QA）与 HaluMem-Long（约 1M token，靠插入 ELI5/数学 QA 等无关对话扩长）；Medium 由 8 名标注者人工抽检 700 会话（正确率 95.70%，论文自报）。
- 许可与生态：仓库徽标为 **CC-BY-NC-ND-4.0**（GitHub API license 字段为 NOASSERTION，以仓库 README 徽标为准）；数据在 HuggingFace IAAR-Shanghai/HaluMem；已中选 **EMNLP 2026 Main**；2026.09 出现社区维护的 EvalPort 适配器。来源：https://github.com/MemTensor/HaluMem（检索 2026-09-25）
- 关键结论（abs 页）：系统「tend to generate and accumulate hallucinations during the extraction and updating stages」，并把错误传导到 QA 阶段；被测系统含 Mem0、Memobase、MemOS、Supermemory、Zep，各阶段分数为论文实验结果（非厂商自报）。来源：https://arxiv.org/abs/2511.03506（检索 2026-09-25）

## 二、MemoryAgentBench（arXiv:2507.05257）

- 论文：*Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions*（Hu, Wang, McAuley）；v1 2025-07-07，v4 2026-06-28；**ICLR 2026** 接收。来源：https://arxiv.org/abs/2507.05257（检索 2026-09-25）
- 四能力构成（abs 页 v4）：Accurate Retrieval、Test-time Learning、Long-range Understanding、Selective Forgetting。注意：官方仓库 README（ICLR 版）列第四能力为 **Conflict Resolution (CR)**——两处一手来源不一致，如实并记。来源：https://github.com/HUST-AI-HYZ/MemoryAgentBench（检索 2026-09-25）
- 构造方式：把既有长上下文数据集改写为**增量多轮交互**格式，另新建 EventQA 与 FactConsolidation 两数据集；采用「inject once, query multiple times」（一段长文本对应多问，提升评测效率）；评测对象含上下文型 Agent、RAG 与带外部记忆模块的 Agent。来源：abs 页 + 仓库 README（均检索 2026-09-25）
- 许可：仓库 **MIT**。来源：GitHub API / 仓库页（检索 2026-09-25）

## 三、映射到兰台六阶段管线的指标清单（每条注来源）

| 兰台阶段 | 可移植指标 | 来源依据 |
|---|---|---|
| 提取 | Memory Recall（+加权版）、Memory Accuracy、Target Precision、Extraction F1、FMR（干扰抵抗） | HaluMem HTML v3 抽取任务定义 |
| 闸门（候选待审） | 无基准直接对应；可用 HaluMem 更新三率的**闸门前置版**：把 N_wrong/N_missed 在裁决时分类统计（方法移植，非原定义，标注为兰台自定义移植） | HaluMem 更新指标（HTML v3）；「宁 miss 不脏写」纪律为兰台内部约束 |
| 入库（写入/版本化） | Updating Accuracy / Hallucination Rate / Omission Rate（对照参考记忆点的增改合并正确性） | HaluMem HTML v3 更新任务定义 |
| 索引 | 无基准直接对应（未确认存在公开操作级索引质量指标）；可作为兰台自定义：召回失败案例中可归因于索引缺失的比例（自定义移植） | 本轮调研未见（如实标注） |
| 召回 | MemoryAgentBench 的 Accurate Retrieval 子集（增量多轮下检索正确性） | MemoryAgentBench abs + README |
| 注入 | QA Hallucination Rate / Omission Rate / Accuracy（端到端，覆盖检索+生成）；Memory Conflict 类问题考冲突裁决；Memory Boundary 考不编造 | HaluMem HTML v3 QA 任务与问题类型 |

## 四、方法论要点与空白

- 共同方法论：把「错误」拆到**操作阶段**计量（存储/检索/注入分摊），而非只看端到端 QA——HaluMem 明言端到端难以定位阶段；MemoryAgentBench 用增量多轮逼出记忆更新/遗忘行为。两文均为可引的操作级范式。来源见上（检索 2026-09-25）。
- 空白：两基准均无「索引质量」与「闸门裁决」阶段的独立指标；兰台若要全六阶段覆盖，闸门与索引两处须自定义（本文件三表已标注）。
- HaluMem 数据集代码库许可为 CC-BY-NC-ND-4.0（禁改作、禁商用）：兰台复用其数据做回归集时须注意 ND 条款；改写需另造数据或仅复用指标定义。
