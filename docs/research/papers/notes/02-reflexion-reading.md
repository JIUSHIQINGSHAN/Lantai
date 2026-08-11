# 《Reflexion: Language Agents with Verbal Reinforcement Learning》精读笔记

> 精读日期：2026-08-11
> 来源链接：https://arxiv.org/html/2303.11366（arXiv HTML 全文；已存 `docs/research/papers/02-reflexion-fulltext.md`）
> arXiv ID：2303.11366

## 1. 元信息

- 标题：Reflexion: Language Agents with Verbal Reinforcement Learning
- 作者：Noah Shinn、Federico Cassano、Edward Berman（Northeastern University）；Ashwin Gopinath（Massachusetts Institute of Technology）；Karthik Narasimhan、Shunyu Yao（Princeton University）
- 机构：Northeastern University / MIT / Princeton University
- 年份：2023（arXiv 预印本 2023-03 发布）
- 发表地：抓取正文未标注；按公开信息为 NeurIPS 2023（poster）（「未确认」）
- arXiv ID：2303.11366
- 全文链接：https://arxiv.org/html/2303.11366

## 2. 一句话核心贡献

提出 Reflexion：不更新 LLM 权重，改用「语言强化」（verbal reinforcement）——把环境的二元/标量反馈经 Self-Reflection 模型转成自然语言反思文本，存入 episodic memory（episodic memory buffer），作为下一轮 trial 的额外上下文，让 agent 在少量 trial 内从失败中自我纠错。

## 3. 研究问题与动机

- 研究问题：以 LLM 为核心的 agent 与外部环境（游戏、编译器、API）交互时，如何「快速、高效」地从试错（trial-and-error）中学习？
- 动机：传统 RL 需要海量训练样本和昂贵的模型微调；而纯 in-context learning 只靠示例教学，缺少「利用失败经验改进下一次尝试」的机制。
- 关键洞察：LLM 的自我反思（self-reflection）是一种 emergent 能力——把稀疏奖励信号放大为「可操作的自然语言经验摘要」，存入长期记忆，等价于给 agent 一个「语义梯度信号」（semantic gradient），如同人类少数几次尝试后复盘形成新计划。
- 反馈信号来源与类型可灵活组合：类型上支持标量或自由文本，来源上支持外部环境或内部自模拟。

## 4. 方法/系统设计（逐步细节）

### 4.1 三模型模块化架构（Figure 2a）

- Actor（M_a）：基于 LLM 的生成器，按状态观测生成文本与动作。实验中探索了 Chain-of-Thought（Wei et al., 2022）与 ReAct（Yao et al., 2023）两种 Actor；并附加记忆组件 mem 提供额外上下文（受 Brooks et al. 2022 的 in-context policy iteration 启发）。
- Evaluator（M_e）：对 Actor 生成的轨迹打分。变体包括：推理任务用精确匹配（exact match, EM）评分；决策任务用针对评价标准设计的预定义启发式函数；决策与编程任务还实验过「用另一个 LLM 实例当 Evaluator」。
- Self-Reflection（M_sr）：LLM 实例。输入稀疏奖励（如二元成功/失败）、当前轨迹、持久记忆 mem，输出比标量奖励信息量更大的细致文字反馈，并存入 mem。示例：决策任务失败时，模型可推断动作 a_i 导致后续错误动作 a_{i+1}、a_{i+2}，并用语言说明本应采取 a_i'，从而在后续 trial 的 t 时刻选择 a_i'。

### 4.2 记忆：短期 + 长期（Memory）

- 短期记忆 = 当前 trial 的轨迹历史（trajectory history）。
- 长期记忆 = Self-Reflection 模型的输出（反思文本）。
- Actor 推理时同时条件于短期与长期记忆；长期记忆被有界约束：mem 保存的经验条数上限记为 Ω，「实践中通常设为 1-3」（usually set to 1-3），以适配 LLM 最大上下文长度。

### 4.3 反思迭代过程（Algorithm 1，Figure 2b）

1. 初始化 Actor/Evaluator/Self-Reflection：M_a、M_e、M_sr；策略 π_θ(a_i|s_i)，其中 θ={M_a, mem}。
2. 用 π_θ 生成初始轨迹 τ_0；Evaluator 打分 r_0=M_e(τ_0)；Self-Reflection 生成 sr_0；mem ← [sr_0]；t=0。
3. while 未通过：生成轨迹 τ_t、Evaluator 打分、生成反思 sr_t、sr_t 追加进 mem、t 自增。
4. 循环直到 Evaluator 判定 τ_t 正确为止。每个 trial 结束后 sr_t 都会追加进 mem。

### 4.4 各任务的关键参数（全部来自原文）

- ALFWorld：134 个环境、6 类任务（找隐藏物、移动物品、用物品操作物品等）；用 ReAct 做动作生成器；LLM 为 GPT-3，few-shot 示例沿用 Yao et al. (2023)；提供 2 条领域内 few-shot 轨迹。
  - 自评触发启发式：同一动作且收到同一反馈超过 3 个 cycle，或当前环境动作数超过 30（低效规划）→ 触发自我反思。
  - 记忆截断：为避免超长 prompt，把记忆截断到最近 3 条自我反思（经验）。
- HotpotQA：100 题；CoT 用 6-shot、ReAct 用 2-shot、self-reflection 用 2-shot；trial 间用 EM 精确匹配给出二元成功信号；记忆大小 3 条经验；temperature 0.7；同一任务连续 3 次失败才放弃重试。
- Programming：用 CoT prompting 生成多样测试（含自然语言描述）→ 用构建抽象语法树（AST）过滤语法合法测试 → 采样 n 条组成测试套件，n 最大为 6；记忆上限为 1 条经验；Rust 通过 MultiPL-E 把 Python 题目翻译过去（HumanEval Rust = HumanEval Python 最难的 50 题）；新基准 LeetcodeHardGym = 40 道 Leetcode hard 题、19 种编程语言，题目均发布于 GPT-4 预训练截止日 2022-10-08 之后。

## 5. 记忆反思机制深挖（本任务重点）

### 5.1 反思的触发条件

- 决策任务（ALFWorld）：失败后触发。baseline（无反思）检测到启发式条件时直接 reset 环境开始新 trial；Reflexion 则先自我反思找错误、更新记忆、再 reset 环境开始新 trial。附录 Figure 5 示例轨迹明确以「Status: Fail」结束，随后生成反思。
- 推理任务（HotpotQA）：每个 trial 后用环境的 EM 二元信号触发「self-reflection loop 放大二元信号」（原文：After each trial, the self-reflection loop is employed to amplify the binary signal）。附录 D 所有反思示例都发生在 Answer is INCORRECT 之后。
- 编程任务：在「失败的单元测试套件评估」之后生成反思（原文：self-reflection step following failed unit test suite evaluations）；消融实验里「无测试生成」的变体则在所有迭代中都必须全程参与、无法提前返回。
- 形式化层面（Algorithm 1）：每个 trial 结束都会生成 sr_t 并追加 mem——即「每轮都反思」，但正文与附录的实例均体现为「失败后反思」；正文未明确写「成功时跳过反思」，触发语义在两种表述之间（原文如此，见第 7 节疑点）。

### 5.2 反思内容如何生成（prompt 结构）

- 输入：稀疏奖励（二元 success/fail 或标量）+ 当前轨迹 + 持久记忆 mem；Self-Reflection 模型「analyzes the set {τ_t, r_t} to produce a summary sr_t」。
- 输出特征：第一人称、含「失败原因 + 未来具体怎么做」的可操作建议。原文示例：
  - ALFWorld：「我应该先找台灯再找杯子（desklamp then mug），而不是先找杯子再找台灯」。
  - HotpotQA ReAct：「我搜错了剧名导致无结果，下次应搜索主演 Gorden Kaye」。
  - HotpotQA CoT：「失败是因为我错误假设两人职业相同，下次应分别调研两人背景」。
- 编程 prompt 结构（Appendix C）：Reflexion Actor 生成按「(Instruction) + (Function implementation) + (Unit test feedback) + (Self-reflection) + (Instruction for next implementation)」组织；Self-Reflection 生成按「(Instruction) + (Function implementation) + (Unit test feedback)」组织，指令要求「只回复改进后的函数体、首行 4 空格缩进、不包含签名」。

### 5.3 反思产出如何存入 episodic memory / 如何被后续轮次使用

- 存储：sr_t 追加进 mem（长期记忆 / episodic memory）；短期记忆是当轮轨迹；mem 受 Ω 上限约束（通常 1-3，编程任务 1、决策与推理任务 3）。
- 使用：下一轮 trial 开始时，Actor「在推理时条件于短期与长期记忆」，把记忆中的经验当作「self-hints」注入上下文；决策任务中 agent 在后续 trial 的 t 时刻据此选择纠正后的动作 a_i'。
- 效果形态：ALFWorld 中长轨迹的早期错误可被识别并提出新动作甚至新长期计划；物品分散在过多容器时靠多轮经验记忆彻底搜索房间（Figure 3 学习曲线：前两轮间立即跃升、随后 11 轮稳步上升至近乎满分）。

### 5.4 与 LLM 权重更新的关系（verbal RL vs 梯度）

- 完全不更新权重、不做微调：策略被参数化为「θ={M_a, mem}」——即记忆编码 + 选定 LLM 参数的组合（原文：parameterizes a policy as an agent's memory encoding paired with a choice of LLM parameters）。
- 反思文本被视为「语义梯度信号」：不是数值梯度，而是给模型一个具体改进方向的文字信号。
- 论文明列优势：1) 轻量、无需微调；2) 支持比标量/向量奖励更细致的反馈形式；3) 提供显式、可解释的 episodic memory；4) 为未来动作给出更明确的提示。劣势：依赖 LLM 自评能力（或启发式），无形式化成功保证。

### 5.5 审查/安全机制

- 论文未设置防幻觉/防污染机制：反思文本直接写入记忆、直接注入后续上下文，无独立校验；作者只承认「依赖 LLM 自评能力、无形式化保证」（原文如此）。
- 编程任务的质量风险被量化分析（Table 2）：自生成测试套件可能 flaky——FP（测试全过但解法错）导致过早提交错误答案；FN（测试错而解法对）相对可容忍，因为 agent 可用反思识别错误测试并保留原代码。MBPP Python 的假阳性执行率 16.3%（表值 FP=0.16）远高于 HumanEval Python 的 1.4%（表值 FP=0.01），直接导致 MBPP Python 上 Reflexion（77.1）不敌 GPT-4 baseline（80.1）。
- 可复现性章节（§8）：作者明确建议「运行自主写代码实验时使用隔离执行环境，因为生成的代码在执行前未经验证」。
- 更广泛影响（§6）：把「verbal RL」视为更可解释、可诊断的路线——例如工具使用场景下可监控自我反思文本以确认意图，再做工具调用。

## 6. 实验与结果（三个基准任务，标注「原文」）

### 6.1 ALFWorld（顺序决策，原文）

- 设置：134 个环境、6 类任务；ReAct + GPT-3；启发式自评 + LLM 二元分类两种自评技术。
- 结果：ReAct + Reflexion 用简单启发式完成 130/134 任务；在 12 个连续 trial 中学会解决额外任务；ReAct-only 在 trial 6 与 7 之间性能停滞；ReAct-only 收敛于 22% 幻觉率、无长期恢复迹象。总体比强 baseline 提升 22 个百分点（absolute 22% in 12 iterative learning steps）。

### 6.2 HotpotQA（语言推理，原文）

- 设置：100 题；CoT 6-shot / ReAct 2-shot / self-reflection 2-shot；EM 二元信号；temperature 0.7；记忆 3 条；连续 3 次失败放弃。
- 结果：Reflexion 在多个学习步上显著优于全部 baseline；ReAct-only、CoT-only、CoT (GT)-only 在 temperature 0.7 下都无法「概率性改进」任何首轮失败的任务。CoT (GT) 对 39% 的问题仍答错，Reflexion 在无 GT 答案下把准确率提升 14%。EPM 消融：self-reflection 比「只加 episodic memory」多 8 个绝对百分点，支持「反思引导的改进优于纯 refinement」。
- 附录 Table 5（原文）：CoT (GT) + text-davinci-003 0.60→0.77；+gpt-3.5-turbo 0.57→0.71；+gpt-4 0.68→0.80；ReAct + text-davinci-003 0.30→0.55；+gpt-3.5-turbo 0.26→0.38；+gpt-4 0.39→0.51。总体比 baseline 提升 20%。

### 6.3 编程（HumanEval / MBPP / LeetcodeHardGym，原文）

- 表 1 Pass@1：HumanEval(PY) 91.0（此前 SOTA 65.8=CodeT+GPT-3.5；当时 SOTA 80.1=GPT-4）；HumanEval(RS) 68.0（SOTA 60.0）；MBPP(PY) 77.1（SOTA 80.1，未超越）；MBPP(RS) 75.4（SOTA 70.9）；LeetcodeHard(PY) 15.0（SOTA 7.5）。比 baseline 提升最高 11%（HumanEval）。
- 表 2 测试生成质量：HumanEval(PY) TP 0.99 / FN 0.40 / FP 0.01 / TN 0.60；MBPP(PY) TP 0.84 / FN 0.59 / FP 0.16 / TN 0.41；HumanEval(RS) TP 0.87 / FN 0.37 / FP 0.13 / TN 0.63；MBPP(RS) TP 0.84 / FN 0.51 / FP 0.16 / TN 0.49。
- 表 3 消融（HumanEval Rust 最难的 50 题，GPT-4）：Base 0.60；去掉测试生成 0.52（低于 baseline，说明无测试引导时反思有害）；去掉 self-reflection 0.60（无提升）；完整 Reflexion 0.68。结论：没有 self-reflection 的「盲目 trial-and-error 调试」在困难任务上无效。
- 表 4（附录）：starchat-beta 上 Baseline 0.26 vs Reflexion 0.26——弱模型无自我纠错收益；「自我修正能力是更强、更大模型的 emergent 性质」（原文）。
- WebShop 局限实验（附录 B.1）：100 个环境、two-shot ReAct+Reflexion，仅 4 轮即终止——无改进、反思无帮助；结论：Reflexion 无法解决需要大量多样性与探索的任务。

## 7. 局限与疑点

- 论文承认的局限（§5）：可能陷入非最优局部极小（local minima）；长期记忆只是有容量上限的滑动窗口（建议未来用向量库或 SQL 数据库扩展）；代码任务里测试驱动开发难以指定精确输入输出映射（非确定性生成函数、调用 API 的非纯函数、硬件相关输出、并行/并发行为）。
- 我读到的可疑/含糊处：
  1. 触发语义含糊：Algorithm 1 说每轮都生成反思并追加 mem，但正文/附录实例只在失败后反思；成功时是否跳过反思未明确（原文如此）。
  2. MBPP Python 落后的解释只给了假阳性率差异（16.3% vs 1.4%），未给出补救方案（原文如此）。
  3. 数字口径小不一致：摘要称 GPT-4 HumanEval 为 80%，表 1 为 80.1；正文称 HumanEval/MBPP baseline pass@1 为 82%/80%，表 2 两列 Base 均为 0.80（原文如此）。
  4. 反思质量无验证机制：反思文本可能给出错误归因（如 WebShop 中「反思无帮助、无直觉」），论文未讨论如何防止反思本身污染记忆。
  5. 上下文成本：反思 + 轨迹全部进 prompt，靠 Ω（1-3 条）硬截断，无检索式记忆；长任务或连续失败时早期教训被丢弃（论文承认此局限）。
- 全文缺失部分：本次抓取为 arXiv HTML 版，正文 1-8 章 + References + 附录 A-D 完整，无缺章；数学公式在转换中部分以 TeX annotation 呈现、偶有乱码（如 r_t=M_e(τ_0) 的下标），不影响数字与结论。

## 8. 对兰台反思模块的启示

映射兰台现有链路（gate / proposal / pending_review 锦囊 / checkpoint 回滚 / conflict_event 账本 / rule lane，见 CONTEXT.md、ADR-0010/0011），「失败后反思 → 沉淀纠错规则」闭环可借鉴以下 6 点：

1. 负反馈触发反思，而不是固定周期触发：兰台已有 `record_feedback`（helped / user_accepted / hallucination_risk → importance delta）。借鉴 Reflexion「失败即反思」——当某记忆被使用时 helped=false 或 hallucination_risk 高，立刻对该记忆触发纠错式反思（产出 update/deprecate 提案），比等固定周期蒸馏更贴合「失败后纠错」语义。
2. 反思产出走 proposal + 锦囊待审，不直接落库：Reflexion 的 sr_t 无审查直接进 mem（这恰是其污染风险）；兰台应把反思文本作为 candidate/proposal（add/update/merge/deprecate）进入 pending_review 锦囊交用户裁决，超龄自动 rejected（与现有 CANDIDATE_TTL 语义一致），守住「宁 miss 不脏写」。
3. 有界经验注入 + 短/长时分离：Reflexion 的 mem 上限 Ω=1-3（编程任务仅 1 条），且短期=轨迹、长期=教训分离。兰台 rule lane 蒸馏时可按「最近 N 条已验证教训」注入（如 N=3），短期记忆用当前对话/轨迹，长期记忆用已 promote 的规则，控制上下文膨胀。
4. 教训要「可执行」并带证据指针：Reflexion 反思示例全部是「失败原因 + 具体行动建议」（先找台灯再找杯子；搜主演而不是剧名）。兰台 rule lane 的 Skill 资产（proposer → promoter 带 structure.steps）正好承载「纠错步骤」；每条教训应关联来源失败轨迹 / conflict_event id 作证据，配合 checkpoint 快照可审计、可回滚。
5. 评估器先行、验证通过才沉淀：Reflexion 编程用自生成单元测试（AST 过滤、最多 6 条）做评估，假阳性（MBPP PY FP 16.3%）会直接污染；兰台 rule lane 蒸馏应要求「验证通过才 promote_procedural」——低置信度提取进 pending_review（已有），高置信度且验证过的才进 rule lane；冲突时走矛盾检测 → archive_conflict，不覆盖旧规则。
6. 纠错循环要有放弃策略：HotpotQA 连续 3 次失败即放弃、ALFWorld 12 轮学习、WebShop 4 轮无改善即终止。兰台可为「同一记忆反复低置信度 / 反复负反馈」设失败上限，达到阈值归档为 rejected 并记入 conflict_event 账本，防止无限重试与脏写回灌。