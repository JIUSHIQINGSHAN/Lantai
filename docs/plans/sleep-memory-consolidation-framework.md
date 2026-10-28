# 【沉潜】闲时夜梦记忆沉淀与折叠压缩理论体系与架构蓝图（Chenqian Framework）

> **代号**：沉潜（Chenqian）  
> **出处**：《荀子》*“沉潜以思，推究事物”*  
> **定位**：基于认知神经科学与最新顶会前沿的 Agent 长程记忆闲时巩固、概念折叠与突触修剪系统。

---

## 一、学术前沿与核心理论脉络（Literature & Foundations）

### 1. 认知神经科学理论基石
* **互补学习系统理论（CLS Theory, McClelland et al.）**：
  - **海马体（Hippocampus）**：快速学习海量情景对话碎片（Episodic Memory / Working Memory），支持即时读写，但抗干扰能力差、容量有限；
  - **新皮层（Neocortex）**：慢速、结构化的语义知识提炼（Semantic Memory），在夜间/闲时通过反复重放（Replay）将情景碎片抽象为高阶规则与常识。
* **突触稳态假说（Synaptic Homeostasis Hypothesis, SHY, Tononi & Cirelli）**：
  - 睡眠的核心功能是**突触修剪与下调（Synaptic Downscaling）**：清除白天产生的边缘微弱噪音，恢复检索的**信噪比（Signal-to-Noise Ratio, SNR）**。

### 2. 2025–2026 前沿顶会与顶级论文
1. **TrustMem (arXiv:2606.25161)** — *《Trustworthy Memory Consolidation for LLM Agents with Long-Term Memory》*
   - **核心贡献**：提出了 **Memory Transition Verifier（记忆迁移校验器）**，防止大模型在记忆合并提纯过程中发生“幻觉捏造（Hallucination）”、“属性篡改（Corruption）”和“关键约束遗漏（Omission）”。
2. **RecMem (arXiv:2605.16045)** — *《Recurrence-based Memory Consolidation for Long-Running LLM Agents》*
   - **核心贡献**：批判了每轮对话都阻塞式压缩的“饥渴式（Eager）”合并，提出**周期性递推沉淀机制**，只有碎片样本达到临界质量（Critical Mass）且处于系统空闲时才执行批处理。
3. **TiMem (arXiv:2601.02845)** — *《Temporal-Hierarchical Memory Consolidation for Long-Horizon Agents》*
   - **核心贡献**：构建**时序-层级记忆金字塔**，将瞬时对话按时间窗聚合为日/周/月层级，保留向上溯源指针（Provenance Pointer）。
4. **Stanford Generative Agents (Park et al., 2023)** — *《Generative Agents: Interactive Simulacra of Human Behavior》*
   - **核心贡献**：奠定了 Agent 记忆**反思（Reflection）**与树状见解（High-level Insights）提取的经典范式。

---

## 二、经典开源项目机制对比

| 项目 | 巩固机制 | 触发时机 | 优点 | 局限性 / 兰台改进 |
| :--- | :--- | :--- | :--- | :--- |
| **Stanford Generative Agents** | Reflection 树状见解生成 | 累积重要性积分达到阈值（150分） | 见解抽象层级高 | 缺乏突触修剪与下调，旧碎片永久保留导致检索冗余 |
| **Letta (MemGPT)** | 上下文超限时递归归档（Archival Memory） | 会话上下文溢出时被迫触发 | 虚拟内存换页思想 | 属于被动触发，缺乏同主题碎片的主动聚类提纯 |
| **Zep (Graphiti)** | 知识图谱边与实体的时间窗口更新 | 消息入库时异步后台处理 | 时效控制严密 | 以实体三元组为主，缺乏自然语言高阶偏好归纳 |
| **兰台【沉潜】(Chenqian)** | **CLS 理论五阶段夜梦沉淀管道** | 闲时调度（凌晨 03:30）+ 按需 API | 兼具聚类提纯、可信校验、突触修剪与秒级可逆快照 | **行业首创**：融合 TrustMem 校验与艾宾浩斯突触修剪 |

---

## 三、【沉潜】五阶段工业级架构设计（Pipeline Architecture）

```
                         【闲时触发 / Cron 03:30】
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │ 阶段一：情景回放与多维拓扑聚类 (Replay & Clustering)      │
        │   - 筛选 active 活跃碎片 (无监督 domain / lane 分区)     │
        │   - TF-IDF / KeyBERT 组内高密度同主题聚类 (size >= 3)    │
        └────────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │ 阶段二：高阶概念综合提纯 (High-Order Synthesis)         │
        │   - 结构化提取：主事实、置信度、重要性、演化理由         │
        │   - 宁 miss 不脏写：LLM 异常则放弃并保持原状            │
        └────────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │ 阶段三：可信迁移校验 (TrustMem Verifier)                 │
        │   - 实体覆盖校验：原始核心实体不得丢失                   │
        │   - 幻觉断言校验：提纯内容不得捏造原始未提及的属性       │
        └────────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │ 阶段四：突触修剪与状态流转 (Synaptic Pruning)           │
        │   - 新主记忆落库 (status="active", decay=1.0)           │
        │   - 原碎片折叠 (status="consolidated", 挂载 source_ids) │
        │   - 极度衰减边缘碎片转入休眠 (status="archived")         │
        └────────────────────────────┬───────────────────────────┘
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────────┐
        │ 阶段五：快照账本与审计留痕 (Checkpoint & Rollback)      │
        │   - 记录 MemoryCheckpoint，支持一键撤销                 │
        │   - 生成当日【沉潜】演化审计报告 (Consolidation Report) │
        └────────────────────────────────────────────────────────┘
```

---

## 四、核心算法与数学模型

### 1. 碎片聚类亲和度判定
对于同一 `(domain, lane)` 分区下的碎片集合 $M = \{m_1, m_2, \dots, m_n\}$，计算其语义重合度：
$$\text{Sim}(m_i, m_j) = \alpha \cdot \text{Cosine}(\vec{e}_i, \vec{e}_j) + (1-\alpha) \cdot \text{Jaccard}(T_i, T_j)$$
当子簇满足 $|C| \ge 3$ 且簇内平均相似度 $\ge 0.75$ 时，激活折叠提纯。

### 2. 突触修剪判据（Synaptic Pruning Threshold）
定义记忆健康度 $H(m)$：
$$H(m) = m.\text{decay\_score} \cdot (1.0 + 0.2 \cdot m.\text{helpful\_count}) - 0.1 \cdot m.\text{hallucination\_risk}$$
当 $H(m) < 0.05$ 且 $m.\text{helpful\_count} == 0$ 且存活天数 $> 7$ 天时，判定为边缘冗余突触，转入 `archived` 休眠。

---

## 五、架构落地方案与演进步骤

1. **框架沉淀（已完成）**：建立 `consolidation_service.py` 核心底座与测试；
2. **可信校验器升级（TrustMem Verifier）**：在提纯阶段引入基于规则与关键词的实体不丢弃校验；
3. **分层折叠演进**：支持从 碎片 $\rightarrow$ 日常习惯 $\rightarrow$ 长期世界观 的多层级折叠树；
4. **可视化集成**：与方向五【悬镜】大屏联动，高亮展示折叠节点与修剪掉的星尘。

---

## 六、MemDream（arXiv:2609.34545）外部方案对照

> 对应票据：`.scratch/v0.24-next-window-2026-10/issues/08-p2-8-sleeptime-memdream-review.md`。
> 证据约束：MemDream（arXiv:2609.34545，2026-09-28）提供一条 partial 级外部印证（LoCoMo answer F1 +4.5，机制包含离线做梦周期、自提问探测、诊断修复与可逆遗忘 soft decay）。本节仅作为方案对照与启发分析，不作为加码实施优先级的依据；在 episode 真实水源（如 dogfooding 长期积累）未解前不动生产代码。

### 1. 外部印证点 vs 兰台设计对照（逐条标注证据等级）

| 维度 / 环节 | MemDream 机制设计 | 兰台「沉潜」既有方案 | 证据等级与对照分析 |
| :--- | :--- | :--- | :--- |
| **离线运行周期** | 独立于在线问答的离线做梦周期（sleep-time dreaming） | 凌晨定时调度 / Cron 03:30（`REFLECT_CRON_HOUR` / `consolidation_service`） | **partial**：印证了「将重度提纯移至系统空闲/离线时段」的大方向，避免在线单步交互时的超长延迟。 |
| **自提问探测与诊断** | 离线自提问（self-questioning）主动探测记忆冲突与空缺，并执行修复 | 阶段二/三提纯与 TrustMem 校验器；`reflector.py` 之 `health_scan`（纯 SQL 规则扫描 open 冲突与过期） | **partial**：MemDream 采用 LLM 生成式自问自答探测隐式冲突；兰台优先采用确定性 SQL 规则探针（轻量单人维护友好），两者在「主动暴露冲突」思路上相互印证。 |
| **遗忘与衰减机制** | 可逆遗忘 soft decay（软衰减），避免硬删除造成不可逆失忆 | 突触修剪（synaptic pruning）：先通过艾宾浩斯软衰减降权，达阈值转 `archived`，配合 Checkpoint 随时可回滚 | **partial**：印证了「不可逆物理硬删除是反模式，软衰减与归档才是健壮生命周期」的兰台立论（ADR-0047/ADR-0052）。 |
| **多层级抽象提纯** | 记忆条目逐步泛化整合 | 碎片 $\rightarrow$ 日常习惯 $\rightarrow$ 长期世界观的递进折叠（本文 §五.3） | **单源（兰台内部规划）**：MemDream 论文摘要及已知公开材料并未主张「分层折叠演进」，该环节仍为兰台自有概念设计，无直接外部印证，保持审慎。 |
| **记忆迁移可信校验** | 诊断修复补丁生成 | 形式化 TrustMem 迁移校验器（实体不丢弃、不凭空臆造） | **单源（兰台引入自 TrustMem 论文）**：MemDream 侧重于 end-to-end 探测修复；兰台结合 TrustMem 的显式实体守卫属于兰台特有工程防御，不混淆归入 MemDream。 |

### 2. 兰台单人维护场景适配分析

1. **计算与 Token 成本边界**：MemDream 依赖大范围自问自答生成（LLM 调用量较大）。在兰台单人自托管架构中，这一做法不可原样照搬；应坚持以方案 B「纯 SQL 健康扫描（零 LLM）为主 + 水位突破后两段提纯/复核为辅」的成本防护策略。
2. **确定性与无后门原则**：MemDream 报出的 LoCoMo answer F1 +4.5 属于端到端任务指标提升，但未开源或未第三方盲测前需保持防御性克制；兰台必须守住「宁 miss 不脏写」与「测试先行、不 mock 冒烟」铁律。
3. **状态结论**：保留为观察级对照；在 episode 影子数据积累达到充分规模（≥100 真实样本）之前，维持仅文档对照、不合并代码的既有裁决。
