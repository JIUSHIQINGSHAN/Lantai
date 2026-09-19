# Iteration 02 — 论文元信息补全（2026-09-19）

## 类型
survey（论文批 1）

## 吸收的证据
经 arXiv 检索核实 15 篇论文元信息（全部为摘要级一手核验）：
- **LightMem** (2510.18866)：Atkinson-Shiffrin 三阶段启发，两级压缩+闲时更新；注意与 2604.07798（SLM 版同名异文）区分。
- **Mem-α** (2509.25911)：RL 学习「存什么/如何结构化/何时更新」。
- **Memory-R1** (2508.19828)：RL 学习 ADD/UPDATE/DELETE/NOOP 操作。
- **MIRIX** (2507.07957)：多智能体记忆系统，多模态。
- **HaluMem** (2511.03506)：首个按记忆操作阶段定位幻觉（捏造/错误/冲突/遗漏）的基准。
- **MemBench** (2506.21605)：多记忆层级+交互场景综合基准。
- **SCM 自控记忆** (2304.13343)：记忆流+控制器。
- **Governed Shared Memory** (2606.24535)：fleet-memory 四大失败模式（越权泄漏/陈旧传播/矛盾存续/血统崩塌）。
- **TRUSTMEM** (2606.25161)：巩固写/改/删的可信度问题。
- **Oblivion** (2604.00131)：遗忘=可及性衰减而非显式删除，读写路径解耦。
- **ImplicitMemBench** (2604.08064)：内隐/程序性记忆基准。
- **EvoMemBench** (2605.18421)：自进化记忆两轴基准。
- **SCM 睡眠巩固** (2604.20943)（同名异文）、**Fine-Mem** (2601.08435)、**AgentMemBench** (2608.00009)。

## 汇总状态
papers.json：17 → **32 篇**。解决开放问题 Q3。（注：本轮无评分动作，总分仍为 3.40——iter-01 的手算误差于 iter-03 程序化复核时才被发现并修正。）

## 决策
- D4：发现三条论文集群——①RL 记忆管理（Mem-α/Memory-R1/Fine-Mem）；②巩固与遗忘（LightMem/Oblivion/睡眠SCM/TRUSTMEM）；③治理化共享记忆（Governed Shared Memory）。留给 R8 纵深综合。
- D5：评测口径出现「操作级/阶段级」趋势（HaluMem 定位存储/检索阶段幻觉）——比端到端 QA 更可诊断，对兰台 dry-run 管线有直接借鉴价值。

## 开放问题
- Q5：Mem-α 等 RL 系是否需要实验级证据才可入兰台路线图（倾向：作为观察项，不做实现承诺）。

## 覆盖
systems=12 papers=32 new_primary_sources=15 score_delta_sum=0 roadmap_churn=0
