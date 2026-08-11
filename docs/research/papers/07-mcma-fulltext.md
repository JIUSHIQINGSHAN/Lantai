# Learning How to Remember: A Meta-Cognitive Management Method for Structured and Transferable Agent Memory（全文存档）

> arXiv:2601.07470v1 [cs.AI] 12 Jan 2026
> 来源: https://arxiv.org/html/2601.07470 （HTML 全文，2026-08-11 抓取；数学公式由 LaTeXML alttext 还原，图片为 SVG 未收录）
> 本文件为精读用全文存档，已去除 arXiv 页面样板（页眉/导航/许可证链接），保留正文、表格、图注与附录。

---

# Learning How to Remember: A Meta-Cognitive Management Method for Structured and Transferable Agent Memory

Sirui Liang1,2,3,
Pengfei Cao1,211footnotemark: 1,
Jian Zhao3,4,
Wenhao Teng5, 

Xiangwen Liao6,
Jun Zhao1,2,
Kang Liu1,222footnotemark: 2
1Institute of Automation, CAS, 2University of Chinese Academy of Sciences, 

3Zhongguancun Academy, 4Zhongguancun Institute of Artificial Intelligence, 

5Department of Gastrointestinal Surgery, Fujian Provincial Cancer Hospital, 

6College of Computer and Data Science, Fuzhou University 

liangsirui2024@ia.ac.cn, jianzhao@zgci.ac.cn, {pengfei.cao,kliu,jzhao}@nlpr.ia.ac.cn

github.com/LiangThree/MCMA  Co-first authors, they contributed equally to this work.  Corresponding author.

###### Abstract

Large language model (LLM) agents increasingly rely on accumulated memory to solve long-horizon decision-making tasks. However, most existing approaches store memory in fixed representations and reuse it at a single or implicit level of abstraction, which limits generalization and often leads to negative transfer when distribution shift. This paper proposes the Meta-Cognitive Memory Abstraction method (MCMA), which treats memory abstraction as a learnable cognitive skill rather than a fixed design choice. MCMA decouples task execution from memory management by combining a frozen task model with a learned memory copilot. The memory copilot is trained using direct preference optimization, it determines how memories should be structured, abstracted, and reused. Memories are further organized into a hierarchy of abstraction levels, enabling selective reuse based on task similarity. When no memory is transferable, MCMA transfers the ability to abstract and manage memory by transferring the memory copilot. Experiments on ALFWorld, ScienceWorld, and BabyAI demonstrate substantial improvements in performance, out-of-distribution generalization, and cross-task transfer over several baselines.

Learning How to Remember: A Meta-Cognitive Management Method for Structured and Transferable Agent Memory

Sirui Liang1,2,3††thanks:   Co-first authors, they contributed equally to this work.,
Pengfei Cao1,211footnotemark: 1,
Jian Zhao3,4††thanks:   Corresponding author.,
Wenhao Teng 5,Xiangwen Liao6,
Jun Zhao1,2,
Kang Liu1,222footnotemark: 21Institute of Automation, CAS, 2University of Chinese Academy of Sciences,3Zhongguancun Academy, 4Zhongguancun Institute of Artificial Intelligence,5Department of Gastrointestinal Surgery, Fujian Provincial Cancer Hospital,6College of Computer and Data Science, Fuzhou Universityliangsirui2024@ia.ac.cn, jianzhao@zgci.ac.cn, {pengfei.cao,kliu,jzhao}@nlpr.ia.ac.cngithub.com/LiangThree/MCMA

## 1 Introduction

Large language model (LLM) agents have recently demonstrated strong performance beyond static question answering Achiam et al. (2023); Bai et al. (2023); Chen et al. (2025b); Tao et al. (2024), increasingly operating in long-horizon, interactive environments that require sustained decision making and environment feedback Qiao et al. (2024); Tan et al. (2025); Zhang et al. (2025b); Chhikara et al. (2025). In these settings, a key capability of agents is to accumulate, organize, and reuse memory, which is also known as procedural memoryFang et al. (2025); Cao et al. (2025). Effectively reusing accumulated memories is essential for enabling agents to solve new tasks efficiently and operate robustly in complex, long-horizon environments Song et al. (2024a).

**Figure: **Figure 1: An example of how MCMA works.

Despite the growing use of memory in LLM-based agents, effective memory reuse remains challenging. Retrieval-based approaches recall trajectories Zheng et al. (2023), sub-tasks Kim et al. (2024), or fine-grained execution steps Zhou et al. (2024), but often rely on surface similarity and fail under environment or task changes Wang et al. (2024b). Summarization Zhao et al. (2024), abstraction Wang et al. (2024b), and hierarchical methods Ye et al. (2025) mitigate noise by organizing memory at higher levels, yet still face an abstraction reuse dilemma. We believe this phenomenon is because fine-grained representations overfit to the seen environments, while overly abstract ones lack actionable guidance. Training-based approaches Song et al. (2024a); Zeng et al. (2024); Wang et al. (2024a) further internalize experience into model parameters to improve generalization, but tightly entangle memory with policy learning, limiting cross-task transfer and risking catastrophic forgetting under domain shifts Chen et al. (2025a). Overall, the aforementioned methods rely on predefined memory representations and fixed or implicit abstraction levels. As a result, agents fail to learn reusable abstractions or adaptively select appropriate levels of memory abstraction for unseen tasks.

To address these challenges, this paper proposes the Meta-Cognitive Memory Abstraction method (MCMA), which treats memory abstraction as a learnable cognitive skill, rather than a static design choice. MCMA operates at a cognitive level by learning how memories should be represented, abstracted, and reused, instead of obtaining fixed or predefined abstract memory itself. As shown in Figure 1, MCMA is built around a Memory Copilot that operates at a meta level, learning to regulate the structure and granularity of memory and managing past successes and failures into reusable abstract memories. To enable this abstraction process to be learned and transferred independently of task-specific behaviors, MCMA decouples memory management from task execution by employing a frozen Task Model solely for action selection.

MCMA follows a four-stage pipeline: collecting trajectories, generating structured memories, training abstraction strategies, and reusing knowledge and ability. The memory copilot is trained via direct preference optimization (DPO) Rafailov et al. (2023) to learn multi-structural memory representations (e.g., tree, chain, or natural language structures) and, crucially, abstraction strategies that support memory reuse.
Guided by cognitive theories of memory abstraction, memory is commonly divided into episodic memory, which stores concrete, context-dependent experiences, and semantic memory, which encodes abstract, organized knowledge Tulving (1972).
MCMA organizes structured memories into multiple abstraction levels, where lower levels retain fine-grained execution details, while higher levels save abstracted knowledge.
When no prior memory is reusable, the memory copilot itself is transferred, preserving the learned abstraction capability. Experiments on ALFWorld (), ScienceWorld (), and BabyAI demonstrate substantial gains in robustness, out-of-distribution generalization, and cross-task transfer.

In summary, our contributions are as follows:

- •
This paper proposes a meta-cognitive memory abstraction method MCMA. By learning structural abstract memory representations and organizing reusable hierarchical abstractions, MCMA transforms memories from inflexible storage into a transferable resource.

- •
MCMA introduces memory copilot, trained via DPO to distill trajectories into structured abstract knowledge that is more suitable for assisting unseen tasks. Crucially, the memory copilot itself can be transferred across domains, reusing the learned ability to abstract, reflect, and organize new tasks.

- •
Extensive experiments on ALFWorld, ScienceWorld, and BabyAI, demonstrating substantial improvements in basic performance, out-of-distribution generalization, and cross-task transfer.

**Figure: **Figure 2: The task model collects raw trajectories, which the memory copilot abstracts into structured knowledge. Preference pairs constructed from downstream task performance are used to train the memory copilot via DPO. Learned memories and the copilot are organized hierarchically to support adaptive memory reuse across tasks.

## 2 Related Work

#### Memory as Content Retrieval or Execution Guidance.

Prior work on memory-augmented LLM agents primarily focuses on storing and retrieving past experience to guide future decision making. Retrieval-based methods, including trajectory-level retrieval Zheng et al. (2023); Kong et al. (2025); Ritchford (2025); Luo et al. (2025), subtask or case-based reuse Kim et al. (2024), and step-wise retrieval aligned with intermediate reasoning states Zhou et al. (2024). While related efforts extract procedural patterns or heuristics from execution histories for planning. These approaches treat memory as reusable content with fixed representations and retrieval granularities. Consequently, retrieved experience often overfits to surface-level similarity and can mislead agents under changes in environment or task, leading to a negative impact.

#### Hierarchical, Reflective, and Learned Experience

Beyond direct retrieval, several works explore hierarchical organization, reflection, or learning from experience to improve agent robustness. Hierarchical and control-based approaches introduce multiple memory levels or regulate memory access to separate high-level planning from low-level execution Ye et al. (2025); Peiyuan et al. (2024); Zhao et al. (2024); Wang et al. (2024c); Zhang et al. (2025a); Xu et al. (2025), while reflection-based methods refine future behavior through error correction Song et al. (2024b); Wang et al. (2025a); Fu et al. (2024); Shinn et al. (2023). Large-scale training efforts further internalize experience by adjusting model parameters to improve generalization Song et al. (2024a); Zeng et al. (2024); Wang et al. (2025b); Yao et al. (2023); Yin et al. (2024). Despite these advances, memory representations and abstraction levels remain largely predefined or implicitly fixed, leaving agents without the ability to learn how experience should be abstracted or which abstraction level is fit for a new task.

## 3 Methodology

As shown in Figure 2, MCMA consists of two functionally distinct models: a Task Model and a Memory Copilot.
Stage 1, the task model collects interaction trajectories by performing the task. Stage 2, the memory copilot transforms raw interaction trajectories into structured and reusable memories through selective structure combination and abstraction. Stage 3, the task model evaluates these memories based on downstream task performance, constructs preference pairs to train the memory copilot, enabling its continual evolution. Stage 4, MCMA organizes the accumulated memories into a hierarchy of abstractions and supports the reuse of either stored knowledge or the memory copilot itself.
For example, in ALFWorld, once a subtask is completed, the memory copilot abstracts the trajectory, and when the agent later encounters a similar task, an appropriate level of abstraction is selected for retrieval.

### 3.1 Trajectory Collection and Preprocessing

The task model collects both successful and failed trajectories from training environments. Raw trajectories are typically long, noisy, and dominated by low-level execution details, making them inefficient for direct reuse. Given a trajectory, execution traces are converted into goal oriented representations via trajectory simplification: 1) Noise pruning: remove redundant or task-irrelevant steps. 2) Subtask segmentation: partition the remaining sequence into coherent subtask units based on changes in the goal (e.g., task "Put a bowl in the dining table" can be divided into subtasks: "Take a bowl from the cabinet", and "Place a bowl on the dining table".). 3) Consistency checking: Verify logical dependencies and temporal ordering between subtasks. 4) Structured processing: Organize subtasks into a tree, with high-level goals as parent nodes and executable skills as leaves. This process preserves both high-level intent and low-level executability while significantly reducing representation complexity. Appendix A.3 provides a detailed preprocessing procedure and examples.

### 3.2 Multi-Structure Memory Generation

The memory copilot learns a meta-level abstraction policy that jointly determines the structural composition and level of memory representations. Since memory representation critically affects reuse and transfer across tasks, we avoid assuming a fixed memory storage structure. Instead, for each trajectory $\tau$, the copilot generates a set of candidate composite structured memories:


> 公式 1: `M_\tau=\{m_\tau^1,m_\tau^2,\dots,m_\tau^N\}   (候选复合结构化记忆集合)`


Each candidate  is constructed by composing and nesting multiple structural primitives drawn from a predefined set:


> 公式 2: `\mathcal{S}=\{\text{natural text, key--value, chain, tree}\}   (结构原语集合)`


rather than selecting a single structure type.  $s(m_\tau^i)$ 为该候选记忆的下游效用评分。 Appendix A.4 provides several structural knowledge examples. Intuitively, different structure capture complementary aspects of experience: tree and chain structures encode high-level dependencies, while key-value pairs and natural language retain fine-grained details. Their composition enables expressive yet reusable memory representations. Each composite structured memory is evaluated by its downstream utility  (Equation 6), and the probability of generating a particular composition is defined as:


> 公式 3: `P_\theta(m_\tau^i\mid\tau)=\frac{\exp(\beta\,s(m_\tau^i))}{\sum_{j=1}^{N}\exp(\beta\,s(m_\tau^j))},\quad \beta>0`


where  denotes the copilot parameters and  is a temperature parameter. This distribution is used to optimize memory selection toward representations with higher expected downstream utility.

To control abstraction granularity, we introduce a continuous abstraction parameter :


> 公式 4: `m_\tau(\alpha)=\text{Memory Copilot}_\theta^{\text{abs}}(\tau,\alpha),\quad \alpha\in[0,1]`


Smaller  preserves execution details, while larger  promotes higher procedure level abstractions. The memory copilot should learn an optimal , which maximizes downstream utility via training:


> 公式 5: `\alpha^*=\arg\max_{\alpha\in[0,1]} s\big(m_\tau(\alpha)\big)`


We collect a diverse set of multi-structure memories with varying abstraction levels to support subsequent memory copilot evolution.

### 3.3 Memory Copilot Evolution

For each trajectory, multiple knowledge candidates are judged on downstream tasks, producing scores:


> 公式 6: `s(m_i)=\begin{cases}0,&\text{task fails},\\ 0.1+0.9\cdot\dfrac{T_{\max}-T_i}{T_{\max}-T_{\min}},&\text{otherwise},\end{cases}\quad T_i=\text{执行步数}`


where  denotes execution length. The  preference pairs with the largest differences in  are used for training. This strategy introduces strong supervision signals while preserving diversity among memory representations. We adopt a two-stage procedure consisting of supervised fine-tuning followed by direct preference optimization (DPO) Rafailov et al. (2023). For a preference pair  derived from the same trajectory , the DPO objective is:



> 公式 7: `$\mathcal{L}=-\log\sigma\Big(\beta\big[\log p_\theta(m^+)-\log p_\theta(m^-)\big]\Big)$`（DPO 目标；$p_\theta(m)$ 为 copilot 在条件 $\tau$ 下生成结构化记忆 $m$ 的似然，$\beta$ 为温度）
where  denotes the likelihood assigned by the memory copilot parameterized by  to generating a structured memory abstraction  conditioned on trajectory . Minimizing this objective encourages the model to assign higher probability to memory representations that lead to more successful and efficient downstream task execution. Success and failure trajectories are processed separately, resulting in two memory copilots with different functions (successful memory summarization and failure memory reflection). During inference,  relevant trajectories are retrieved using character-level matching on task descriptions, then abstracted by the copilot before being provided to the task model.

### 3.4 Hierarchical Abstraction and Cross-Task Reusing

After learning instance-level memory representations, memories are organized into a hierarchy:


> 公式 8: `\mathcal{H}=\{H_0,H_1,\dots,H_L\},\quad L=2\ \text{(本工作中)}`


where lower levels store detailed memories,  retaining raw trajectories, low levels (e.g., ) encoding episodic memories with execution details, and higher levels (e.g., ) capturing abstract knowledge such as scripts ( in our work). We perform similarity clustering based on the vector representation of the task description. High-level abstractions are obtained by merging lower-level representations while suppressing task-specific details to retain shared goals. For a new task , memory reuse is performed via:


> 公式 9: `m_{\text{reuse}}=\arg\max_{m\in H_\ell,\ \ell\in[0,L]} \text{sim}(\tau_{\text{new}}, m)`


High-similarity tasks are supported by low-level, detailed episodic memories, whereas low-similarity tasks use higher-level abstract memories. Abstracted knowledge is incorporated into the prompt to assist the model when solving similar tasks.

Crucially, under extreme distribution shifts where no stored memory is directly reusable, MCMA transfers the memory copilot itself rather than specific trajectories. Let  and  denote the training and novel task distributions. The memory copilot learns a transferable abstraction strategy  by optimizing


> 公式 10: `\theta^*=\arg\max_\theta \mathbb{E}_{\tau\sim\mathcal{T}_{\text{train}}}\big[s(\text{Abstraction}_\theta(\tau,\alpha))\big]`


By training,  can be directly applied to new tasks  to generate abstract memories, enabling the model to generalize and transfer effectively even in the absence of relevant stored trajectories.

In this way, the memory copilot learns not only what to remember but how experience should be abstracted to maximize its future utility, enabling robust transfer across tasks and domains.

## 4 Experiment


```
Model | Method | ALFWorld | Science World
Seen | Unseen | Dev | Test
Acc(%) | Step | Acc(%) | Step | Reward | Reward
Qwen3-8B | No Memory | 55.00 | 33.15 | 56.33 | 33.06 | 21.26 | 19.18
ReAct | 59.29 ↑4.29 | 31.04 ↓2.11 | 64.93 ↑8.60 | 30.58 ↓2.48 | 21.00 ↓0.26 | 18.41 ↓0.77
Raw Tra | 67.14 ↑12.14 | 24.75 ↓8.40 | 72.39 ↑16.06 | 25.01 ↓8.05 | 20.17 ↓1.09 | 17.61 ↓1.57
TRAD | 64.29 ↑9.29 | 28.48 ↓4.67 | 63.43 ↑7.10 | 29.76 ↓3.30 | 30.16 ↑8.90 | 27.42 ↑8.24
ExpeL | 69.28 ↑14.28 | 24.73 ↓8.42 | 72.39 ↑16.06 | 24.16 ↓8.90 | 26.43 ↑5.17 | 23.12 ↑3.94
MCMA | 79.29 ↑24.29 | 21.24 ↓11.91 | 80.60 ↑24.27 | 20.57 ↓12.49 | 31.17 ↑9.91 | 29.17 ↑9.99
Qwen3-32B | No Memory | 62.86 | 29.85 | 66.42 | 30.36 | 45.10 | 43.69
ReAct | 63.57 ↑0.71 | 31.15 ↑1.30 | 72.39 ↑5.97 | 28.75 ↓1.61 | 45.44 ↑0.34 | 44.05 ↑0.36
Raw Tra | 74.29 ↑11.43 | 21.92 ↓7.93 | 78.36 ↑11.94 | 21.39 ↓8.97 | 44.82 ↓0.28 | 41.70 ↓1.99
TRAD | 74.29 ↑11.43 | 24.77 ↓5.08 | 79.10 ↑12.68 | 24.73 ↓5.63 | 42.77 ↓2.33 | 42.00 ↓1.69
ExpeL | 78.57 ↑15.71 | 20.40 ↓9.45 | 77.61 ↑11.19 | 21.23 ↓9.13 | 44.97 ↓0.13 | 43.78 ↑0.09
MCMA | 90.71 ↑27.85 | 17.78 ↓12.07 | 90.30 ↑23.88 | 18.00 ↓12.36 | 51.95 ↑6.85 | 48.60 ↑4.91
```


**Figure: **Table 1: Performance comparison of the Qwen3 model. We highlight the best and second best results. Green arrows () indicate performance improvement, and Red arrows () indicate decline.


```
Model | Seen | Unseen
GPT-4o-mini | 42.86 | 48.51
MCMA | 63.57 ↑20.71 | 63.43 ↑14.92
Gemini-2.5-flash | 61.43 | 67.91
MCMA | 84.29 ↑22.86 | 88.06 ↑20.15
```


**Figure: **Table 2: Reuse memory copilot of Qwen3-32B on Gemini-2.5-flash and GPT-4o-mini on ALFWorld task.

### 4.1 Experimental Setup

#### Datasets.

We evaluate MCMA on two long-horizon text-based embodied reasoning benchmarks: ALFWorld Shridhar et al. (2020b) and ScienceWorld Wang et al. (2022). ALFWorld evaluates household task completion from textual observations and provides seen and unseen splits. The seen split contains the same task types, objects, and room categories as training but varies object configurations. The unseen task instances are executed in rooms that never appear during training. ScienceWorld focuses on multi-step scientific reasoning under complex text-based dynamics and follows a standard Dev / Test split. Both benchmarks emphasize long-horizon reasoning and generalization beyond memorizing surface-level execution patterns, requiring effective abstraction, experience reuse, and adaptation to unseen environments.

#### Metrics.

For ALFWorld, we follow the standard evaluation method and report results on both seen and unseen task splits. Performance is measured by task success rate (Acc%) and average execution steps, where higher accuracy and fewer steps indicate better performance. For ScienceWorld, we evaluate on the Dev and Test splits and report the average task reward score, which reflects the progress of sub-goals.

#### Models.

We conduct experiments using two backbone task models: Qwen3-8B Yang et al. (2025) and Qwen3-32B. The memory copilot uniformly uses Qwen3-4B. In all settings, the task model is frozen during training and evaluation, and all memory-related learning is isolated within the memory copilot.

#### Baselines.

We compare MCMA against several memory configurations: 1) No Memory, where the agent operates purely reactively without access to prior experience.  2) ReActYao et al. (2022), adopt a process of fully observing the environment and thinking before making a decision. 3) Raw Trajectory, which retrieves and injects raw trajectories into memory. 4) TRADZhou et al. (2024), retrieves relevant expert steps via thought matching and aligning them with localized temporal context for each steps. 5) ExpeLZhao et al. (2024), which extracts high-level natural language insights from past experiences and leverages retrieved successful trajectories as in-context demonstrations during inference. For the ALFWorld task, MCMA uses both the successful memory summary and the failed memory reflection memory copilot. Considering that the ScienceWorld environment is more variable, and summarizing successful memories often has negative effects, this task only used the failed memory reflection memory copilot.

### 4.2 Main Results

As shown in Table 1, MCMA consistently achieves the strongest performance across all environments and model scales. Compared to all baselines, MCMA significantly improves task success rates while reducing execution steps, indicating more efficient and reliable long-horizon planning.

Consistent gains across seen and unseen tasks.
MCMA delivers large and stable improvements over no-memory and prior memory-based baselines in both in-domain and out-of-distribution settings. For example, on ALFWorld with Qwen3-8B, MCMA improves success rates by nearly 25% over the no-memory baseline, with comparable gains on unseen tasks. MCMA maintains similar improvements across seen and unseen scenarios, highlighting the strong generalization of its meta-cognitive memory abstraction.

Improved efficiency and scalability across models.
MCMA consistently reduces the average number of execution steps across benchmarks, producing efficient and concise action sequences. These benefits are particularly pronounced for smaller models: on ScienceWorld, MCMA yields nearly a 10% absolute gain on Qwen3-8B, while still providing consistent improvements on Qwen3-32B. This indicates that memory abstraction effectively assists capacity limited model and remains powerful even as model scale increases. We further test the performance of the memory copilot trained for Qwen3-32B on the closed-source model Gemini-2.5-Flash and GPT-4o-mini. As shown in Table 2, the performance improvement is close to that achieved on Qwen3-32B, indicating that MCMA has good transferability and performs well on closed-source models.

### 4.3 Ablation Study


```
Model | Configuration | Seen | Unseen
Acc(%) | Step | Acc(%) | Step
Qwen3-8B | Natural Language Know | 74.63 ↓4.66 | 23.37 ↑2.13 | 72.39 ↓8.21 | 24.88 ↑4.31
Chain Know | 77.14 ↓2.15 | 22.57 ↑1.33 | 75.27 ↓5.23 | 23.55 ↑2.98
MCMA | 68.57 ↓10.72 | 23.69 ↑2.45 | 72.39 ↓8.21 | 23.85 ↑3.28
MCMA | 72.86 ↓6.43 | 25.15 ↑3.91 | 71.64 ↓8.96 | 25.82 ↑5.25
MCMA | 79.29 | 21.24 | 80.60 | 20.57
Qwen3-32B | Natural Language Know | 77.86 ↓12.85 | 20.50 ↑2.72 | 82.09 ↓8.21 | 21.75 ↑3.75
Chain Know | 86.14 ↓4.57 | 19.10 ↑1.32 | 84.33 ↓5.97 | 20.24 ↑2.24
MCMA | 76.43 ↓14.28 | 21.84 ↑4.06 | 83.25 ↓7.05 | 23.97 ↑5.97
MCMA | 82.14 ↓8.57 | 22.46 ↑4.68 | 84.33 ↓5.97 | 21.95 ↑3.95
MCMA | 90.71 | 17.78 | 90.30 | 18.00
```


**Figure: **Table 3: Component and Memory Representation ablation study of Qwen3-32B settings on ALFWorld .

#### Component Ablations.

To analyze the contribution of individual components in the memory copilot, we conduct ablation studies on ALFWorld, focusing on successful memory summarization (Sum) and failure memory reflection (Ref). As shown in Table 3, each component independently improves performance: summarization reduces execution steps by abstracting procedural knowledge, while reflection helps avoid recurring errors. Combining both components achieves the best performance across all settings and model scales, yielding higher success rates with fewer steps, particularly under distribution shift. These results suggest that summarization and reflection capture complementary aspects of experience "learning what to do and what to avoid", and are jointly critical for robust generalization.


```
Variant | Acc(%) | Step
MCMA | 83.58 ↓7.13 | 22.53 ↑4.75
MCMA | 86.56 ↓4.15 | 19.75 ↑1.97
MCMA | 90.71 | 17.78
```


**Figure: **Table 4: Comparison with MCMA using untrained Qwen3-4B / Gemini-2.5-flash as memory copilot.

#### Memory Representation and Training Ablations.

We further examine the impact of memory representation and training by comparing MCMA with variants that store experiences as natural language or chain-structured knowledge generated by DPO-trained memory copilots. As shown in Table 3 (Nautral Language Know and Chain Know), both representations yield substantial gains over the no-memory baseline (average accuracy 64.64%), suggesting that the proposed abstraction strategy is relatively insensitive to specific surface structures. Nevertheless, a consistent performance gap remains compared to the full MCMA (MCMA), highlighting the importance of structured organization for effective memory utilization. In addition, when deploying untrained memory copilots Qwen3-4B or more powerful Gemini-2.5-flash in unseen settings, performance drops 7.13% and 4.15% (Table 4), indicating that DPO training is essential for equipping the memory copilot with transferable abstraction and generalization capabilities.

### 4.4 Structured Memory Analyze

**Figure: **Figure 3: The impact of the number of knowledge provided by MCMA in the ALFWorld task.

The quantity of provided knowledge significantly impacts the model’s performance. As illustrated in Figure 3, MCMA achieves optimal results on ALFWorld when 4–5 structural knowledge are provided. Insufficient knowledge fails to equip the task model with enough foresight to adapt diverse scenarios, while excessive knowledge imposes a heavy contextual overhead that disturbs decision-making. Notably, MCMA’s structured knowledge is significantly more concise than original traces, it allows for a higher density of knowledge items within the prompt.

**Figure: **Figure 4: Analyze knowledge structure of MCMA. 

In addition, we count all nested structural knowledge. At the top-level, the predominant structures are Tree and Chain (Specifically, Tree structures comprised approximately 70% of these top-level arrangements, while Chain structures accounted for about 20%). This hierarchical organization effectively provides high-level directional guidance to the task model. Furthermore, within both Tree and Chain structures, a wide variety of sub-structures, such as Key-Value pairs and Natural Language descriptions, are nested, enabling a more fine-grained and explicit representation of detailed information. The overall distribution of all knowledge structure types is comprehensively illustrated in Figure 4.a. We further identify tasks characterized by Tree and Chain top-level structure and replace the knowledge representations with other structural formats. As shown in Figure 4.b, this led to a decline in performance, which underscores the effectiveness of the structure selection strategy studied by MCMA.

### 4.5 Cross-Task Knowledge Transfer

Previous sections have shown that MCMA generalizes effectively within the same task distribution. This section extends our investigation to evaluate how MCMA performs in entirely novel environments.

To evaluate the effectiveness of hierarchical knowledge transfer under distribution shift, we extend our evaluation to the BabyAI benchmark Chevalier-Boisvert et al. (2018). BabyAI requires agents to interpret and execute compositional, synthetic natural language instructions in a partially observable 2D gridworld, involving navigation, object manipulation, and environment interaction. Although BabyAI shares high-level similarities with ALFWorld in terms of spatial reasoning and object-centric interaction, it introduces a substantial domain shift, particularly in observation modalities (2D grid states vs. textual descriptions) and action granularity.


```
Variant | Acc(%) | Reward
Base | 16.67 | 14.24
Abstract Level 1 | 13.54 ↓3.13 | 12.32 ↓1.92
Abstract Level 2 | 17.71 ↑1.04 | 15.33 ↑1.09
```


**Figure: **Table 5: Apply ALFWorld knowledge on BabyAI.

As shown in Table 5, transfer performance is strongly correlated with abstraction granularity. Fine-grained Level 1 knowledge leads to a 3.13% performance drop, indicating negative transfer caused by mismatched low-level interaction details. In contrast, higher-level Level 2 abstractions improve the baseline by 1.04%, suggesting that abstract knowledge better suppresses domain-specific noise and preserves reusable task semantics. These results demonstrate that higher-level abstractions are essential for effective generalization across distantly related tasks.

### 4.6 Cross-Domain Copilot Transfer


```
Method | ScienceWorld | ALFWorld
Base | 19.18 | 56.33
Sci Copilot | 29.17 ↑9.99 | 61.19 ↑4.86
ALF Copilot | 27.43 ↑8.25 | 71.64 ↑15.31
Mix Copilot | 29.85 ↑10.67 | 69.40 ↑13.07
```


**Figure: **Table 6: Copilots transfer across domains.

We further explore a more challenging scenario: how to reuse memory when a new task has no correlation with existing memories. MCMA achieves this by transferring the memory copilot, which reuses the capability to organize and abstract knowledge rather than the knowledge itself. We evaluate this via a cross-task transfer between ALFWorld and ScienceWorld. As shown in Table 6, transferring copilots between ALFWorld and ScienceWorld consistently outperforms the baseline. ALFWorld copilot outperforms ScienceWorld copilot in overall performance, we speculate this is because ALFWorld collecting more training data. Notably, the Mix Copilot (trained on mix data collected from ALFWorld and ScienceWorld tasks) proves effective for both domains, particularly providing substantial gains for the task with limited training resources (ScienceWorld). These results provide compelling evidence that MCMA facilitates generalization by learning a transferable meta-cognitive skill for memory management.

## 5 Conclusion

We propose MCMA, a meta-cognitive memory abstraction method that treats experience reuse as a learnable problem. By decoupling task execution from memory management, MCMA uses a transferable memory copilot trained via preference optimization to abstract and organize interaction trajectories. Experiments on ALFWorld, ScienceWorld, and BabyAI show that MCMA improves task performance, generalization, and cross-task transfer, highlighting the importance of learning how to remember in LLM-based agents.

## Limitations

MCMA has achieved good performance and generalization, but it also comes with several limitations. First, MCMA introduces additional computational overhead during training, as constructing preference supervision requires generating and evaluating multiple candidate abstractions for each trajectory. While this cost is incurred offline and the learned memory copilot can be reused across tasks, the overall training pipeline remains more expensive than retrieval-based or single-representation memory methods. Second, although MCMA organizes experience into a hierarchy of abstraction levels to support flexible reuse, the current reuse process relies on a manually designed strategy to select which abstraction level should be applied to a new task. This limits full end-to-end adaptivity and suggests an important direction for future work: learning abstraction-level selection jointly with memory structuring and reuse.

## Ethical consideration

Our research explores the reuse of Large Language Models (LLMs) memory. Despite undergoing additional fine-tuning in various experiments, these models retain ethical and social risks inherent in their pretraining data. Notably, open-source LLMs may incorporate private or contentious data during the training phase, thereby raising additional ethical concerns.

## References

- J. Achiam, S. Adler, S. Agarwal, L. Ahmad, I. Akkaya, F. L. Aleman, D. Almeida, J. Altenschmidt, S. Altman, S. Anadkat, et al. (2023)Gpt-4 technical report.
arXiv preprint arXiv:2303.08774.
Cited by: §1.

- J. Bai, S. Bai, Y. Chu, Z. Cui, K. Dang, X. Deng, Y. Fan, W. Ge, Y. Han, F. Huang, et al. (2023)Qwen technical report.
arXiv preprint arXiv:2309.16609.
Cited by: §1.

- Z. Cao, J. Deng, L. Yu, W. Zhou, Z. Liu, B. Ding, and H. Zhao (2025)Remember me, refine me: a dynamic procedural memory framework for experience-driven agent evolution.
arXiv preprint arXiv:2512.10696.
Cited by: §1.

- S. Chen, T. Zhu, Z. Wang, J. Zhang, K. Wang, S. Gao, T. Xiao, Y. W. Teh, J. He, and M. Li (2025a)Internalizing world models via self-play finetuning for agentic rl.
arXiv preprint arXiv:2510.15047.
Cited by: §1.

- S. Chen, S. Lin, X. Gu, Y. Shi, H. Lian, L. Yun, D. Chen, W. Sun, L. Cao, and Q. Wang (2025b)Swe-exp: experience-driven software issue resolution.
arXiv preprint arXiv:2507.23361.
Cited by: §1.

- M. Chevalier-Boisvert, D. Bahdanau, S. Lahlou, L. Willems, C. Saharia, T. H. Nguyen, and Y. Bengio (2018)Babyai: a platform to study the sample efficiency of grounded language learning.
arXiv preprint arXiv:1810.08272.
Cited by: §A.1,
§4.5.

- P. Chhikara, D. Khant, S. Aryan, T. Singh, and D. Yadav (2025)Mem0: building production-ready ai agents with scalable long-term memory.
arXiv preprint arXiv:2504.19413.
Cited by: §1.

- M. Côté, Á. Kádár, X. Yuan, B. Kybartas, T. Barnes, E. Fine, J. Moore, R. Y. Tao, M. Hausknecht, L. E. Asri, M. Adada, W. Tay, and A. Trischler (2018)TextWorld: a learning environment for text-based games.
CoRRabs/1806.11532.
Cited by: §A.1.

- R. Fang, Y. Liang, X. Wang, J. Wu, S. Qiao, P. Xie, F. Huang, H. Chen, and N. Zhang (2025)Memp: exploring agent procedural memory.
arXiv preprint arXiv:2508.06433.
Cited by: §1.

- Y. Fu, D. Kim, J. Kim, S. Sohn, L. Logeswaran, K. Bae, and H. Lee (2024)Autoguide: automated generation and selection of state-aware guidelines for large language model agents.
CoRR.
Cited by: §2.

- M. Kim, V. Bursztyn, E. Koh, S. Guo, and S. Hwang (2024)Rada: retrieval-augmented web agent planning with llms.
In Findings of the Association for Computational Linguistics ACL 2024,
 pp. 13511–13525.
Cited by: §1,
§2.

- Y. Kong, D. Shi, G. Yang, C. Huang, X. Li, S. Jin, et al. (2025)MapAgent: trajectory-constructed memory-augmented planning for mobile task automation.
arXiv preprint arXiv:2507.21953.
Cited by: §2.

- H. Luo, S. Dai, C. Ni, X. Li, G. Zhang, K. Wang, T. Liu, and H. Salam (2025)Agentauditor: human-level safety and security evaluation for llm agents.
arXiv preprint arXiv:2506.00641.
Cited by: §2.

- F. Peiyuan, Y. He, G. Huang, Y. Lin, H. Zhang, Y. Zhang, and H. Li (2024)Agile: a novel reinforcement learning framework of llm agents.
Advances in Neural Information Processing Systems37,  pp. 5244–5284.
Cited by: §2.

- S. Qiao, R. Fang, N. Zhang, Y. Zhu, X. Chen, S. Deng, Y. Jiang, P. Xie, F. Huang, and H. Chen (2024)Agent planning with world knowledge model.
Advances in Neural Information Processing Systems37,  pp. 114843–114871.
Cited by: §1.

- R. Rafailov, A. Sharma, E. Mitchell, C. D. Manning, S. Ermon, and C. Finn (2023)Direct preference optimization: your language model is secretly a reward model.
Advances in neural information processing systems36,  pp. 53728–53741.
Cited by: §1,
§3.3.

- E. Ritchford (2025)Optimizing llm-agents with history-driven task planning.
Journal of Computer Technology and Software4 (3).
Cited by: §2.

- N. Shinn, B. Labash, and A. Gopinath (2023)Reflexion: an autonomous agent with dynamic memory and self-reflection.
arXiv preprint arXiv:2303.11366.
Cited by: §2.

- M. Shridhar, J. Thomason, D. Gordon, Y. Bisk, W. Han, R. Mottaghi, L. Zettlemoyer, and D. Fox (2020a)ALFRED: A Benchmark for Interpreting Grounded Instructions for Everyday Tasks.
In The IEEE Conference on Computer Vision and Pattern Recognition (CVPR),
External Links: LinkCited by: §A.1.

- M. Shridhar, X. Yuan, M. Côté, Y. Bisk, A. Trischler, and M. Hausknecht (2020b)Alfworld: aligning text and embodied environments for interactive learning.
arXiv preprint arXiv:2010.03768.
Cited by: §A.1,
§4.1.

- Y. Song, W. Xiong, X. Zhao, D. Zhu, W. Wu, K. Wang, C. Li, W. Peng, and S. Li (2024a)Agentbank: towards generalized llm agents via fine-tuning on 50000+ interaction trajectories.
arXiv preprint arXiv:2410.07706.
Cited by: §1,
§1,
§2.

- Y. Song, D. Yin, X. Yue, J. Huang, S. Li, and B. Y. Lin (2024b)Trial and error: exploration-based trajectory optimization for llm agents.
arXiv preprint arXiv:2403.02502.
Cited by: §2.

- Z. Tan, J. Yan, I. Hsu, R. Han, Z. Wang, L. Le, Y. Song, Y. Chen, H. Palangi, G. Lee, et al. (2025)In prospect and retrospect: reflective memory management for long-term personalized dialogue agents.
In Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers),
 pp. 8416–8439.
Cited by: §1.

- Z. Tao, T. Lin, X. Chen, H. Li, Y. Wu, Y. Li, Z. Jin, F. Huang, D. Tao, and J. Zhou (2024)A survey on self-evolution of large language models.
arXiv preprint arXiv:2404.14387.
Cited by: §1.

- E. Tulving (1972)“Episodic and semantic memory,” in organization of memory.
(No Title),  pp. 381.
Cited by: §1.

- H. Wang, J. Wang, C. T. Leong, and W. Li (2025a)Steca: step-level trajectory calibration for llm agent learning.
arXiv preprint arXiv:2502.14276.
Cited by: §2.

- P. Wang, Z. Li, N. Zhang, Z. Xu, Y. Yao, Y. Jiang, P. Xie, F. Huang, and H. Chen (2024a)Wise: rethinking the knowledge memory for lifelong model editing of large language models.
Advances in Neural Information Processing Systems37,  pp. 53764–53797.
Cited by: §1.

- R. Wang, P. Jansen, M. Côté, and P. Ammanabrolu (2022)Scienceworld: is your agent smarter than a 5th grader?.
arXiv preprint arXiv:2203.07540.
Cited by: §A.1,
§4.1.

- S. Wang, Y. Wu, and Z. Xu (2025b)Cogito, ergo ludo: an agent that learns to play by reasoning and planning.
arXiv preprint arXiv:2509.25052.
Cited by: §2.

- Z. Wang, J. Mao, D. Fried, and G. Neubig (2024b)Agent workflow memory.
arXiv preprint arXiv:2409.07429.
Cited by: §1.

- Z. Z. Wang, J. Mao, D. Fried, and G. Neubig (2024c)Agent workflow memory.
arXiv preprint arXiv:2409.07429.
Cited by: §2.

- Z. Xu, Y. Liu, Y. Yin, M. Zhou, and R. Poovendran (2025)Kodcode: a diverse, challenging, and verifiable synthetic dataset for coding.
arXiv preprint arXiv:2503.02951.
Cited by: §2.

- A. Yang, A. Li, B. Yang, B. Zhang, B. Hui, B. Zheng, B. Yu, C. Gao, C. Huang, C. Lv, et al. (2025)Qwen3 technical report.
arXiv preprint arXiv:2505.09388.
Cited by: §4.1.

- S. Yao, J. Zhao, D. Yu, N. Du, I. Shafran, K. R. Narasimhan, and Y. Cao (2022)React: synergizing reasoning and acting in language models.
In The eleventh international conference on learning representations,
Cited by: §4.1.

- W. Yao, S. Heinecke, J. C. Niebles, Z. Liu, Y. Feng, L. Xue, R. Murthy, Z. Chen, J. Zhang, D. Arpit, et al. (2023)Retroformer: retrospective large language agents with policy gradient optimization.
arXiv preprint arXiv:2308.02151.
Cited by: §2.

- S. Ye, C. Yu, K. Ke, C. Xu, and Y. Wei (2025)H2R: hierarchical hindsight reflection for multi-task llm agents.
arXiv preprint arXiv:2509.12810.
Cited by: §1,
§2.

- D. Yin, F. Brahman, A. Ravichander, K. Chandu, K. Chang, Y. Choi, and B. Y. Lin (2024)Agent lumos: unified and modular training for open-source language agents.
In Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers),
 pp. 12380–12403.
Cited by: §2.

- A. Zeng, M. Liu, R. Lu, B. Wang, X. Liu, Y. Dong, and J. Tang (2024)Agenttuning: enabling generalized agent abilities for llms.
In Findings of the Association for Computational Linguistics: ACL 2024,
 pp. 3053–3077.
Cited by: §1,
§2.

- G. Zhang, M. Fu, G. Wan, M. Yu, K. Wang, and S. Yan (2025a)G-memory: tracing hierarchical memory for multi-agent systems.
arXiv preprint arXiv:2506.07398.
Cited by: §2.

- Z. Zhang, Q. Dai, X. Bo, C. Ma, R. Li, X. Chen, J. Zhu, Z. Dong, and J. Wen (2025b)A survey on the memory mechanism of large language model-based agents.
ACM Transactions on Information Systems43 (6),  pp. 1–47.
Cited by: §1.

- A. Zhao, D. Huang, Q. Xu, M. Lin, Y. Liu, and G. Huang (2024)Expel: llm agents are experiential learners.
In Proceedings of the AAAI Conference on Artificial Intelligence,
Vol. 38,  pp. 19632–19642.
Cited by: §A.2,
§1,
§2,
§4.1.

- L. Zheng, R. Wang, X. Wang, and B. An (2023)Synapse: trajectory-as-exemplar prompting with memory for computer control.
arXiv preprint arXiv:2306.07863.
Cited by: §1,
§2.

- R. Zhou, Y. Yang, M. Wen, Y. Wen, W. Wang, C. Xi, G. Xu, Y. Yu, and W. Zhang (2024)Trad: enhancing llm agents with step-wise thought retrieval and aligned decision.
In Proceedings of the 47th International ACM SIGIR Conference on Research and Development in Information Retrieval,
 pp. 3–13.
Cited by: §A.2,
§1,
§2,
§4.1.

## Appendix A Appendix

### A.1 Dataset Details

#### ALFWorld.

Shridhar et al. (2020b)
We conduct experiments using the ALFWorld environment Shridhar et al. (2020b), a framework designed to bridge abstract text-based reasoning and embodied execution. ALFWorld aligns the text-based TextWorld engine Côté et al. (2018) with the visually grounded ALFRED benchmark Shridhar et al. (2020a) by utilizing the Planning Domain Definition Language (PDDL) to synchronize state dynamics across modalities. The dataset encompasses six types of compositional household tasks (e.g., Pick & Place, Clean & Place, Heat & Place) distributed across 120 interactive scenes, including kitchens, bedrooms, bathrooms, and living rooms. To evaluate generalization, ALFWorld defines strict data splits: a Seen set containing 140 task instances in rooms encountered during training, and an Unseen set containing 134 tasks in novel rooms with different layouts and object placements, thereby testing the agent’s ability to perform zero-shot transfer in out-of-distribution environments.

**Figure: **Figure 5: A simplified trajectory of an agent performing an Examine in Light task in ALFWorld.

#### ScienceWorld.

Wang et al. (2022) ScienceWorld is a complex interactive text environment designed to evaluate the procedural reasoning capabilities of agents within the context of an elementary school science curriculum. Distinct from static question-answering benchmarks, ScienceWorld simulates a dynamic physical world with underlying engines for thermodynamics, electrical circuits, chemistry, and biological life cycles. The benchmark consists of 30 diverse tasks across 10 topics, ranging from testing electrical conductivity to conducting genetic experiments, requiring agents to synthesize declarative scientific knowledge into multi-step action sequences (e.g., heating a beaker, connecting a battery). There are 1819 tasks in the test set and 1796 tasks in the dev set. Furthermore, the environment enforces rigorous evaluation through thousands of parametric variations, testing an agent’s ability to generalize scientific concepts to novel objects and scenarios.

**Figure: **Figure 6: An example of ScienceWorld.

**Figure: **Figure 7: A step-by-step trajectory for the “Find a living thing” task in ScienceWorld. The score (right) updates incrementally as the agent completes sub-goals: identifying the correct object (focus), acquiring it, navigating, and placing it in the target container.

#### BabyAI

Chevalier-Boisvert et al. (2018) We evaluate our transfer ability on the BabyAI platform Chevalier-Boisvert et al. (2018), a benchmark designed to study the sample efficiency of grounded language learning agents. BabyAI is built upon the MiniGrid framework, featuring a partially observable 2D gridworld where agents perceive a  symbolic local view. The platform defines a curriculum of 19 levels of increasing difficulty, ranging from single-instruction tasks (e.g., GoToObj) to complex composite missions involving navigation and manipulation (e.g., BossLevel).

Figure 8 is an example of BabyAI’s 2D grid image. To bridge the modality gap, we employ a deterministic translator that converts the agent’s  egocentric symbolic observation into natural language. The agent is virtually anchored at the bottom-center coordinate  of the observable grid. For every salient object  located at  (excluding static background elements like walls), we calculate its relative spatial position using Manhattan distances: longitudinal offset $d_{fwd}=6-y_i$ and lateral offset $d_{lat}=x_i-3$. These coordinates are mapped to egocentric linguistic templates (e.g., “A red ball is 3 steps forward and 1 step to the left”), converting the sparse tensor into a dense descriptive list of visible entities. The final prompt fed to the LLM is constructed by concatenating the static system instructions, the specific Mission Goal, a chronological History of Actions, and the Current Observation generated above. The system instruction strictly defines the admissible action space (e.g., move forward, toggle) and enforces a structured JSON output format. This design ensures the model grounds its decision-making in both the immediate visual context and the temporal trajectory of the episode, allowing for chain-of-thought reasoning before action selection.

**Figure: **Figure 8: A 2D grid image example of BabyAI.

**Figure: **Figure 9: A step-by-step trajectory for the “Go to the green key” task in BabyAI. Unlike ScienceWorld, BabyAI typically uses sparse rewards, meaning the score (right) remains 0.00 until the specific goal condition is met at the final step.

### A.2 Baseline Details

#### ExpeL

ExpeL Zhao et al. (2024) is a non-parametric learning framework that enables LLM agents to improve autonomously through experience gathering and knowledge extraction. During a training phase, the agent collects successful and failed trajectories via trial-and-error (utilizing ReAct and Reflexion). It then abstracts cross-task knowledge into natural language insights (e.g., guidelines or constraints) and stores successful trajectories in a vector database. At inference time, ExpeL augments the agent’s context with these extracted insights and dynamically retrieves the top- most similar successful past trajectories to serve as few-shot examples, thereby leveraging both abstract rules and concrete experiences to enhance decision-making without gradient updates.

#### TRAD

TRAD (Thought Retrieval and Aligned Decision) Zhou et al. (2024) is a novel framework designed to enhance Large Language Model (LLM) agents in sequential decision-making tasks by addressing the limitations of traditional trajectory-level retrieval, such as irrelevant context and context window constraints. The framework comprises two core components: Thought Retrieval and Aligned Decision. The former employs a step-wise retrieval mechanism where the agent generates a “thought”—an abstraction of the current state—to query a memory of expert demonstration steps, ensuring high relevance and minimizing noise. The latter augments these retrieved steps with their temporal neighbors through techniques including Temporal Expansion, Relative Order Marks, and History Alignment, thereby recovering essential contextual dynamics lost in single-step retrieval. Empirical evaluations on benchmarks such as ALFWorld and Mind2Web demonstrate that TRAD significantly outperforms state-of-the-art baselines by effectively balancing context sufficiency with noise reduction.

### A.3 Task Preprocess Details

This section details the task preprocessing pipeline. As illustrated in Algorithm 1, raw trajectories are transformed into a hierarchical tree structure  through a recursive coarse-to-fine decomposition strategy.
The process initiates with trajectory pruning, which eliminates redundant noise from the raw sequence.
Subsequently, the sequence undergoes temporal decomposition to identify distinct sub-goals, followed by a consistency verification step where segments are validated and labeled to ensure logical coherence.
This decomposition is applied recursively: segments containing three or more actions are subject to further partitioning, whereas shorter segments are preserved as atomic leaf nodes, thereby yielding a structured representation of long-horizon tasks. A raw trajectory is shown in Figure 10, and a processed example is shown in 11.

```
Algorithm 1: Hierarchical Task Trajectory Decomposition
输入: 完整任务轨迹 T_raw
输出: 层次任务树 Ψ
1. 初始化: T_clean ← A_prune(T_raw)   # 轨迹去噪
2. 创建根节点 n_root，Ψ.setRoot(n_root)
3. RecursiveDecompose(n_parent):
     τ ← n_parent.trajectory
     valid ← False
     while not valid:                    # 一致性检查
        S ← A_split(τ)                   # 子目标切分
        for each 子任务 s_i ∈ S, 标签 l_i ∈ L:
            Create node n_child with content s_i and label l_i
            Ψ.addChild(n_parent, n_child)
            if Length(s_i) ≥ 3:          # 递归粗到细分解
                RecursiveDecompose(n_child)
```**Figure: **Figure 10: A task trajectory example in ALFWorld.

**Figure: **Figure 11: A processed example in ALFWorld.

### A.4 Examples of Structured Knowledge

In this section, we provide some structured knowledge examples derived from MCMA (tree structure, chain structure, key-value structure, natural language structure, and nest structure. As shown in Figure 14, 15, 16, 17, 18). Figure 13 shows the prompt used for guiding memory copilot to generate structural knowledge, which outlines the role and instructions, the required JSON structures and the few-shot example provided to the model.

### A.5 Task Prompt

Figure 12 shows our task prompt for ALFWorld and ScienceWorld.

**Figure: **Figure 12: Task Prompt for our tasks.

### A.6 Reuse Memory Copilot on Same Series LLMs

Since the data acquisition for each memory copilot is driven by the specific performance of the task model, the copilot is inherently aligned with the model’s capabilities, thereby providing critical assistance in challenging scenarios. However, training a dedicated copilot entails significant computational overhead. To address this, we investigate the transferability of the memory copilot across different models within the same lineage. As evidenced in Tables 7 and 8, the memory copilot optimized for Qwen3-32B demonstrates robust performance when deployed on other LLMs in the same series.


```
Type | Config | Performance
Step | Acc
Base | Default | 38.67 | 0.40
Sum | Default | 31.39 ↓7.28 | 0.51 ↑11%
Trained | 31.31 ↓7.36 | 0.55 ↑15%
Reflection | Default | 35.18 ↓3.49 | 0.44 ↑4%
Trained | 32.99 ↓5.68 | 0.51 ↑11%
MCMA | Default | 30.08 ↓8.59 | 0.54 ↑14%
Trained | 28.37 ↓10.30 | 0.59 ↑19%
```


**Figure: **Table 7: Reuse memory copilot trained for Qwen3-32B on Qwen3-4B.


```
Type | Config | Performance
Step | Acc
Base | Default | 27.93 | 0.62
Sum | Default | 24.68 ↓3.25 | 0.66 ↑4%
Trained | 22.51 ↓5.42 | 0.69 ↑7%
Reflection | Default | 27.35 ↓0.58 | 0.65 ↑3%
Trained | 25.16 ↓2.77 | 0.71 ↑9%
MCMA | Default | 24.25 ↓3.68 | 0.66 ↑4%
Trained | 23.70 ↓4.23 | 0.69 ↑7%
```


**Figure: **Table 8: Reuse memory copilot trained for Qwen3-32B on Qwen2.5-72B.

### A.7 Hierarchical Abstraction Processing

This section details our approach to handling hierarchical knowledge. The process begins with semantic embedding generation, where we encode each knowledge entry by concatenating its name and textual description into a unified semantic vector. To model the relationships between these entries, we proceed with sparse semantic graph construction. Specifically, we compute the cosine similarity between knowledge vectors and construct a k-Nearest Neighbor (k-NN) graph, retaining only the top-k (k=10) strongest connections to form a sparse adjacency matrix.

Building upon this topology, we employ hierarchical clustering to identify latent semantic communities within the graph. The final consolidation is achieved through a two-phase strategy: first, intra-cluster fusion merges redundant or highly similar knowledge points within each identified cluster; subsequently, inter-cluster abstraction re-aggregates these fused clusters to derive higher-level abstract concepts, thereby forming a structured and concise knowledge hierarchy.

As shown in Figure 19 and Figure 20, we provide the examples of different level of knowledge provided by MCMA. Lower-level knowledge focuses on complete task details, while higher-level knowledge focuses on capturing common problems at higher levels.

**Figure: **Figure 13: The prompt to guide memory copilot in generating structured knowledge.

**Figure: **Figure 14: Tree structure knowledge examples.

**Figure: **Figure 15: Chain structure knowledge examples.

**Figure: **Figure 16: Key-Value structure knowledge examples.

**Figure: **Figure 17: Natural Language structure knowledge examples.

**Figure: **Figure 18: Nested structure knowledge examples.

**Figure: **Figure 19: An Example Knowledge of Abstract Level 1.

**Figure: **Figure 20: An Example Knowledge of Abstract Level 2.

Generated  on Mon Jan 12 12:24:10 2026 by LaTeXML[IMAGE:Mascot Sammy]


