# 《Voyager: An Open-Ended Embodied Agent with Large Language Models》精读笔记

> 精读日期：2026-08-11
> 来源链接：https://arxiv.org/html/2305.16291（arXiv HTML 全文，已存 `docs/research/papers/05-voyager-fulltext.md`）
> arXiv ID：2305.16291

## 1. 元信息

- 标题：Voyager: An Open-Ended Embodied Agent with Large Language Models
- 作者：Guanzhi Wang、Yuqi Xie、Yunfan Jiang、Ajay Mandlekar、Chaowei Xiao、Yuke Zhu、Linxi "Jim" Fan、Anima Anandkumar（∗ 并列一作；† 并列指导；@ 通讯作者）
- 机构：1 NVIDIA、2 Caltech、3 UT Austin、4 Stanford、5 UW Madison（按原文脚注）
- 年份：2023（arXiv 预印本，2023-05-25 提交）
- 发表地：抓取全文未标注会议/期刊（原文如此）；公开记录显示其后发表于 NeurIPS 2023（该条非全文信息，「未确认」）
- arXiv ID：2305.16291
- 全文链接：https://arxiv.org/html/2305.16291（项目主页：https://voyager.minedojo.org）

## 2. 一句话核心贡献

提出 Voyager——首个 LLM 驱动的具身终身学习智能体：通过黑盒调用 GPT-4（零参数微调），以「自动课程 + 可执行代码技能库 + 迭代提示机制」三组件在 Minecraft 中持续自我探索、习得可复用可组合的技能，并在新世界中零样本泛化到未见任务。

## 3. 研究问题与动机

- 背景问题：经典 RL/模仿学习方法在开放世界探索（systematic exploration）、可解释性、泛化上困难；而现有 LLM 智能体（ReAct/Reflexion/AutoGPT 等）不是「终身学习者」——无法在长时间跨度内渐进地获取、更新、积累、迁移知识（原文 §1，引用 [31, 32]）。
- 领域特殊性：Minecraft 没有预定义终点或固定剧情，是程序化生成的开放 3D 世界；要求玩家按科技树逐步解锁（木→石→铁→钻石工具）。
- 作者论证的「有效终身学习智能体应具备人类玩家的三项能力」（原文 §1）：
  1. 根据当前技能水平与世界状态提议合适任务（例如身处沙漠应优先学收获沙子和仙人掌，而非先学铁）；
  2. 基于环境反馈精炼技能，并把掌握了的技能提交到记忆中供将来相似情境复用（「打僵尸 ≈ 打蜘蛛」）；
  3. 自我驱动地持续探索、寻找新任务。
- 关键设计选择：用「代码」作为动作空间而非底层马达指令——程序天然可以表示时间上扩展（temporally extended）与组合（compositional）的动作，是长时程任务的关键（原文 §1）。

## 4. 方法/系统设计（逐步细节）

整体架构（原文 Figure 2）：三个组件闭环——自动课程提议任务 → 迭代提示机制用代码完成任务 → 技能库存储/检索可复用技能；技能库增长反过来支撑更复杂任务。

### 4.1 自动课程（Automatic Curriculum，原文 §2.1 + 附录 A.3）

- 作用：用 GPT-4 内在的互联网级知识，持续产出「难度递增但可达成」的新任务流；bottom-up 展开，随探索进度和智能体状态自适应。作者称之为一种「in-context 形式的新颖性搜索」（novelty search）。
- 输入 prompt 的组件（原文 §2.1 / A.3.1）：
  1. 指令与约束，例如「My ultimate goal is to discover as many diverse things as possible ... The next task should not be too hard since I may not have the necessary resources or have learned enough skills to complete it yet.」；
  2. 当前状态：库存（inventory 字典）、装备、附近方块（32 格距离内的方块名集合）、附近实体（32 格距离内，如 pig/cat/villager/zombie）、看到过的箱子（未开箱内容为 Unknown）、生物群系 biome、时间（sunrise/day/noon/sunset/night/midnight）、血量与饥饿条（最大值 20）、三维坐标 (x,y,z)；
  3. 已完成任务与失败任务列表（反映探索进度与能力边界）；
  4. 附加上下文：用 GPT-3.5 自问自答（self-ask question → 每问配一个 concept 用于从 MineDojo wiki 知识库检索最相关文档 → 喂给 GPT-3.5 自答）；wiki 知识库可选，GPT-3.5 本身已了解 Minecraft 机制（原文 A.3.2）。用 GPT-3.5 而非 GPT-4 处理这类标准 NLP 任务是出于预算考虑。
- 参数：课程 temperature = 0.1（鼓励任务多样性）；其余组件 temperature = 0（原文 §3.1）。
- 预热计划（Warm-up Schedule，原文 Table A.1）：按已完成任务数渐进注入信息——0 个任务后注入核心库存（仅 log/planks/stick/crafting table/furnace/dirt/coal/pickaxe/sword/axe）、装备、附近方块、位置；5 个任务后加入附近实体；7 个后加入完整库存；10 个后加入最近看到的其他方块与生物群系；15 个后加入血量、饥饿、时间与附加上下文。

### 4.2 技能库（Skill Library，原文 §2.2 + 附录 A.4）

- 表示：每个技能 = 一段可执行代码（Mineflayer JavaScript），脚手架出完成特定任务的「时间上扩展的动作」；代码具备通用性、可解释性、普适性。
- 存储：技能库是向量数据库。key = 程序描述（description，由 GPT-3.5 生成）的 embedding 向量；value = 程序本身（原文 Figure 4 图注）。embedding 用 text-embedding-ada-002（原文 §3.1）。
- 代码生成 prompt 的 12 个组件（原文 A.4.1）：代码生成准则、控制原语 API（含 mineBlock 32 格内、craftItem、smeltItem、killMob timeout=300、exploreUntil maxTime=60、箱子存取、pathfinder goals 等）、检索到的相关技能、上一轮代码、环境反馈、执行错误、critique、当前状态、任务、任务上下文（GPT-3.5 一般性求解建议，实际由课程 QA 机制提供）、CoT 推理要求（先解释上一轮代码为何失败→给出分步计划→最后生成代码）。
- 技能命名示例（原文 A.4.3）：`craftWoodenPlanks`（通用型，内部会调用 `mineWoodLog`）与 `mineTenCobbledDeepslateBelowY0`（具体型）。

### 4.3 迭代提示机制（Iterative Prompting，原文 §2.3 + 附录 A.5）

- 三类反馈驱动自改进：
  1. 环境反馈（Environment Feedback）：程序执行中间进展；在控制原语 API 中植入 `bot.chat()` 输出，例如「I cannot make an iron chestplate because I need: 7 more iron ingots」；prompt 也要求生成代码时使用该函数；
  2. 执行错误（Execution Errors）：程序解释器返回的非法操作/语法错误，用于修 bug；
  3. 自我验证（Self-Verification）：实例化另一个 GPT-4 作为 critic——输入当前状态 + 任务，输出 JSON `{"reasoning": ..., "success": bool, "critique": ...}`；既检查成功也（失败时）给出改进建议，作者称比 Reflexion 的 self-reflection「更全面」。
- 迭代循环（原文 §2.3 与 Algorithm 1）：生成代码 → 执行拿环境反馈与执行错误 → 并入下一轮 prompt 精炼 → 直到 self-verification 判定完成 → 新技能写入技能库并向课程要新目标；若卡住超过 **4 轮代码生成**，则放弃当前任务、向课程要另一个任务。
- 伪代码要点（Algorithm 1）：每轮都 `retrieve_skills(task, environment_feedback)`；`if success: add_skill(code) + add_completed_task(task) else: add_failed_task(task)`。
- token 优化：不做多轮对话，把 system prompt 与 user prompt 拼接成单次调用（原文 A.2）。

## 5. 技能沉淀/验证机制深挖（本任务重点）

### 5.1 写入技能库的准入条件：仅 self-verification 判定成功

- 原文 §2.3 明确：「This iterative process repeats until self-verification validates the task's completion, at which point we add this new skill to the skill library and ask the automatic curriculum for a new objective.」
- 伪代码（Algorithm 1）同样只有 `if success: skill_manager.add_skill(code)`——**失败路径绝不写库**，只把任务记入 failed 列表。这等价于「验证通过才沉淀」。
- 验证者形态：独立 GPT-4 critic 实例（非写码 agent 自己），few-shot 示例教会它按状态推断成功信号，例如：库存出现 `rotten_flesh` = 成功击杀僵尸；饥饿值 20.0 = 成功进食；种植任务看 nearby blocks 中是否有 farmland/wheat；矿/冶炼任务只查库存（原文 A.5.2 完整 prompt）。验证输入的状态刻意排除「最近看到的其他方块」和「附近实体」（对判定任务完成无用，原文 A.5.1）。
- 没有人工审查环节：唯一 gate 是自动 self-verification；原文未提及任何人工复核/抽检流程。

### 5.2 失败时的迭代：硬上限 4 轮 + 任务级重试

- 每任务最多 **4 轮代码生成**（`for i in range(4)`）；「If the agent gets stuck after 4 rounds of code generation, then we query the curriculum for another task.」（原文 §2.3）。
- 4 轮后仍失败 → 任务进 failed 列表，**不是永久丢弃**：课程会结合当前状态择机重试该任务（原文 §4：「The automatic curriculum has the flexibility to reattempt this task at a later time.」）。
- 对照：消融变体 w/o Self-Verification 也是固定 4 轮（3 轮 refine + 初始 1 轮，原文 B.3）——说明 self-verification 的价值主要在于「提前判断成功、尽早停轮」与「失败时给 critique」，而非改变轮数上限。

### 5.3 检索复用：描述 embedding + top-5 注入

- 检索 query = GPT-3.5 生成的求解任务一般建议（general suggestion）+ 环境反馈，拼成 query context 后取 **top-5** 相关技能（原文 Figure 4 图注）。
- top-5 技能以 in-context 示例形式注入代码生成 prompt（原文 §2.2：「we include the top-5 relevant skills in the prompt for synthesizing a new skill」）。
- 检索质量（原文 Table A.4，309 个样本）：top-1 准确率 80.2±3.0%、top-2 89.3±1.8%、top-3 93.2±0.7%、top-4 95.2±1.8%、top-5 96.5±0.3%。

### 5.4 技能组合：嵌套复用 + 泛化性约束

- 新技能代码里可直接调用旧技能（示例 `craftWoodenPlanks` 内部调用 `mineWoodLog`），形成技能 → 复杂技能的组合链；技能库由此「compound the agent's abilities rapidly」（摘要）。
- 代码生成准则显式要求泛化与复用：「Your function will be reused for building more complex functions. Therefore, you should make it generic and reusable.」（原文 §2.2）——与命名示例（通用 `craftWoodenPlanks` vs 具体 `mineTenCobbledDeepslateBelowY0`）呼应。
- 技能描述（用于检索的 key）由 GPT-3.5 生成，保证描述与查询文本同源同分布。

### 5.5 审查/去重/版本管理：原文未覆盖（诚实标注）

- 全文未提及技能去重、冲突消解、版本管理或技能库容量上限；技能以描述 embedding 为 key 持续追加，未见删除/合并机制（原文如此——属空白点而非已解决项）。
- 唯一的质量兜底是 self-verification；且作者承认它「偶尔也会失败」（如不把击杀蜘蛛掉落的 spider string 视为成功信号，原文 §4）。
- 安全性讨论只在 Broader Impacts（原文 §7）：部署到物理机器人需人工施加安全约束；Minecraft 环境本身安全无害。

## 6. 实验与结果（数字均标注「原文」）

### 6.1 设置（原文 §3.1 / B.1）

- 模型：gpt-4-0314（主实验）+ gpt-3.5-turbo-0301 + text-embedding-ada-002；temperature 0（课程 0.1）。
- 环境：MineDojo + Mineflayer JS API；在 Mineflayer 函数中植入大量 `bot.chat()` 提供环境反馈、try-catch 保证持续执行；bot 死亡后原地附近复活并保留库存；每次执行后回收合成台与熔炉。
- 指标口径：每方法 3 次 trial；探索与科技树任务上限 160 次 prompting iterations，零样本任务上限 50 次；baseline 统一任务为「explore the world and get as many items as possible」。

### 6.2 探索能力（原文 §3.3 + B.4.1）

- Voyager 在 160 次 prompting iterations 内发现 **63 种独特物品**（原文）。
- 摘要（原文）：获得独特物品数量是 prior SOTA 的 **3.3×**；baseline 对比：ReAct 单 trial 最少仅 2-4 种物品，Reflexion 4-5 种，AutoGPT 11-24 种（原文 B.4.1 逐 trial 物品清单）。

### 6.3 科技树掌握（原文 Table 1；数字 = 平均提示迭代次数，括号为 3 次 trial 成功率）

| 方法 | 木工具 | 石工具 | 铁工具 | 钻石工具 |
|---|---|---|---|---|
| ReAct | N/A (0/3) | N/A (0/3) | N/A (0/3) | N/A (0/3) |
| Reflexion | N/A (0/3) | N/A (0/3) | N/A (0/3) | N/A (0/3) |
| AutoGPT | 92±72 (3/3) | 94±72 (3/3) | 135±103 (3/3) | N/A (0/3) |
| Voyager w/o 技能库 | 7±2 (3/3) | 9±4 (3/3) | 29±11 (3/3) | N/A (0/3) |
| **Voyager** | **6±2 (3/3)** | **11±2 (3/3)** | **21±7 (3/3)** | **102 (1/3)** |

- 相对 baseline，Voyager 解锁木工具快 **15.3×**、石工具快 **8.5×**、铁工具快 **6.4×**，且是唯一解锁钻石层级的方法（原文 §3.3）。

### 6.4 地图覆盖（原文 §3.3 / Figure 7）

- Voyager 移动距离为 baseline 的 **2.3×**，穿越多样地形（如 meadow/desert/savanna/bamboo_jungle/dripstone_caves/ocean 等）；baseline 常困于局部区域（原文 B.4.2 逐 trial 地形清单）。

### 6.5 零样本泛化到未见任务（原文 Table 2；新世界 + 清空库存，上限 50 次）

| 方法 | 钻石镐 | 金剑 | 岩浆桶 | 指南针 |
|---|---|---|---|---|
| ReAct / Reflexion / AutoGPT | N/A (0/3) | N/A (0/3) | N/A (0/3) | N/A (0/3) |
| AutoGPT + Voyager 技能库 | 39 (1/3) | 30 (1/3) | N/A (0/3) | 30 (2/3) |
| Voyager w/o 技能库 | 36 (2/3) | 30±9 (3/3) | 27±9 (3/3) | 26±3 (3/3) |
| **Voyager** | **19±3 (3/3)** | **18±7 (3/3)** | **21±5 (3/3)** | **18±2 (3/3)** |

- 亮点：技能库可即插即用——注入 AutoGPT 后其部分任务从 0/3 提升到 1/3~2/3（原文 §3.3 称技能库是「plug-and-play asset」）。

### 6.6 消融（原文 §3.4 + Figure 9）

- 自动课程换成随机课程 → 独特物品数 **-93%**；手动课程（作者手写「挖钻石」10 步序列）也逊于自动课程。
- 去掉技能库 → 后期出现平台期（plateau），无法持续变复杂。
- **去掉 self-verification → 独特物品数 -73%**（三类反馈中最关键）。
- GPT-3.5 替代 GPT-4 生成代码 → 独特物品数少 **5.7×**（GPT-4 编码能力有代差）。
- 去掉环境反馈 / 执行错误也有下降（原文 Figure 9，具体数值仅在图中，未表格化——原文如此）。

### 6.7 其他

- 检索评估：309 样本，top-1 80.2% → top-5 96.5%（原文 Table A.4，见 5.3）。
- 模型稳健性：gpt-4-0613 与 gpt-4-0314 性能大致相同（原文 B.4.5 / Figure A.4）。
- 人类反馈：人类当 critic（≈self-verification）或当课程（≈自动课程）时，Voyager 能建造 Nether Portal 与房子等复杂 3D 结构（原文 §3.5 / Figure 10）。

## 7. 局限与疑点

### 原文承认的局限（原文 §4）

- 成本：GPT-4 API 比 GPT-3.5 贵 **15×**，而代码生成质量又必须依赖 GPT-4（GPT-3.5/开源模型达不到）。
- 不精确：尽管有迭代提示，仍有卡住失败的情况；self-verification 偶尔误判（例：不把 spider string 当作打败蜘蛛的成功信号）。
- 幻觉：课程偶发提议不存在的物品（如「copper sword」「copper chestplate」）；代码生成偶发把圆石当燃料（游戏中无效）、调用控制原语 API 中不存在的函数。

### 我读到的可疑/含糊处

- 技能库无去重/版本/冲突管理：以 embedding 为 key 无限追加，同质技能累积、检索噪音如何控制未讨论（对比兰台已有 dedup/merge/deprecate，反而是 Voyager 的缺口）。
- 消融主数字只有文字（-93%、-73%、5.7×），Figure 9 的具体曲线数值未表格化，难以精确复现。
- 零样本任务仅 4 个（钻石镐/金剑/岩浆桶/指南针），样本量小；「3.3× 独特物品」与「63 unique items in 160 iterations」是同一实验的两个口径，摘要的「prior SOTA」具体指哪个 baseline 未在摘要点明。
- 钻石工具仅 1/3 trial 成功（102 次），说明最高难度任务方差大、接近能力上限。
- 主实验只有 gpt-4-0314 一个模型，0613 仅在附录验证「大致相同」；未报告总 token 消耗/美元成本，只有 15× 相对倍数。
- 「技能描述由 GPT-3.5 生成」+「查询文本由 GPT-3.5 建议 + 环境反馈组成」的嵌入一致性（同源同分布）是检索高准确率的隐性前提，原文未做跨模型/跨语言的鲁棒性测试。

### 全文覆盖情况

- 我抓取的是 arXiv HTML v1（2023-05-25 提交）完整版本：正文 §1-§8、参考文献 [1]-[92]、附录 A（算法/提示词全文/预热表/技能示例/自验证 prompt）、附录 B（设置/基线/消融/完整结果）。图片为渲染图，正文仅保留图注与图片链接；后续 arXiv 修订版本差异「未确认」。

## 8. 对兰台反思模块的启示

兰台现有链路对照（依据仓库 `CONTEXT.md` 与 `lantai/` 代码）：gate（`gate/decision.py`：提取置信度阈值→REJECT、新颖度<0.15→WORKING_ONLY 走合并、有 actions→PROMOTE_PROCEDURAL、硬矛盾→ARCHIVE_CONFLICT+冲突账本）、proposer（`evolution/proposer.py`：候选→提案 add/update/merge/deprecate，actions 资产化为 `structure{name,description,steps}`）、promoter 落库、pending_review 锦囊队列（用户裁决，超龄归档 rejected）、候选创建时余弦去重、检索（hybrid FTS5+向量）。Voyager 的可借鉴点：

1. **「验证通过才沉淀」升级 procedural lane 的 gate**：Voyager 只在 self-verification 通过后写技能库，且验证者是独立的 critic 实例、带 few-shot 成功信号示例。兰台现在对 `cand.actions` 非空即 PROMOTE_PROCEDURAL（在置信度/新颖度/矛盾检查通过后）；可借鉴「行为级验证」——为技能类候选加一步可执行性/自洽性检查（步骤引用的资源、技能是否在库、步骤是否可达成），把 gate 从「提取置信度」提升为「沉淀前验证」，呼应 AGENTS.md「宁 miss 不脏写」。
2. **失败迭代上限 + 失败留档供重试，不污染库**：Voyager 每任务最多 4 轮，失败任务进 failed 列表、由课程择机重试。映射：gate REJECT / 锦囊裁决 rejected 的候选应保留失败原因（decision 已带 reason 文本），并支持「冷却后重提/再提案」路径，避免一次否决终身不审；「失败不写库」与「宁 miss 不脏写」同构，可直接写入 rule/procedural lane 的蒸馏规则。
3. **检索注入 top-k + 可量化的检索质量度量**：Voyager 以「描述 embedding」检索 top-5 技能注入生成 prompt，并公开 top-1→top-5 准确率（80.2%→96.5%，309 样本）作为可信度证据。兰台 skill asset（structure.steps 非空）应明确定义检索键（技能名+描述+适用条件），并建立类似 top-k accuracy 的评估集来衡量「注入技能是否命中」，而不是只看召回打分。
4. **技能组合与依赖管理**：Voyager 新技能可直接调用旧技能（`craftWoodenPlanks` 调 `mineWoodLog`），并强制「generic and reusable」。映射：兰台 procedural skill 的 `structure.steps` 应支持步骤级引用已沉淀技能；promoter 应用提案时做依赖检查——被引用技能若被 deprecate/归档，需联动提示或阻止，避免孤儿步骤。
5. **critique 反馈进提案/裁决**：Voyager 失败时 critic 输出可操作的 critique，下一轮 refine 直接消费。映射：兰台 REJECT/WORKING_ONLY 决策与锦囊裁决页应携带「修正建议」字段（gate reason 已有文本基础），用户/后续 agent 可据此「修正后重提」，形成闭环而非静默丢弃。
6. **渐进信息注入（warm-up）与反例**：Voyager 按任务数分档注入上下文（0/5/7/10/15 个任务后逐步放开），避免早期 prompt 过载。映射：兰台提取/检索 prompt 可按记忆库规模渐进扩展上下文；同时 Voyager「无去重无版本」是反例——兰台应保留现有 dedup + proposal merge/update/deprecate + checkpoint 回滚，不要学其无限增长。

**一句话总结**：Voyager 验证了「验证通过才沉淀 + 失败有上限重试 + 描述向量检索 top-k 注入 + 技能嵌套组合」这套终身学习闭环在开放世界的有效性；兰台可把其中「行为级验证 gate」「失败留档重试」「技能依赖检查」三点直接落入 rule/procedural lane 与提案链路，并用量化的 top-k 命中率度量检索质量。

