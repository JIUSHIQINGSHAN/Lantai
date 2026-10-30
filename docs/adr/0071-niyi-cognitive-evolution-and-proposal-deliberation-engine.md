# ADR-0071: 拟议（Niyi）——认知演化提案审议与因果变化推演引擎

**日期**: 2026-10-30  
**状态**: Accepted  
**决策者**: 大哥、Claude  
**来源**: 认知演化闭环深化演进与 ADR-0013 意象候选转正（`proposal 演化提案 -> 拟议`）

---

## 1. 背景

在智能体认知记忆演化流程中，候选记忆（`MemoryCandidate`）或系统反思产物通过提案（`MemoryProposal`）实施对存量记忆的增、改、合、废。既有系统存在以下关键演化风险与架构空白：

1. **孤立修改导致因果拓扑断裂**：既有提案在 `add`、`update`、`merge` 或 `deprecate` 时，通常仅核验单一目标记忆本身，未对记忆附带的因果拓扑边（`causes` / `leads_to` / `supports` / `refines`）进行预先模拟与连带影响评估，容易在记忆废弃或更正后引发下游推理断言悬空；
2. **未决提案冲突与竞争缺乏审计**：在多轮高频对话与异步反思中，针对同一实体或目标记忆可能并发积累多个处于 `pending` 状态的竞争提案，缺乏租户级全局审计与互斥排查能力；
3. **提示注入防御（樊篱贯穿）**：提案在被呈现给下游 LLM、控制台审查或认知上下文注入时，未经樊篱包裹的文本极易被利用实施提示注入；
4. **命名与规范治理**：落实 ADR-0013 意象池候选转正规范，正名「**拟议**」（Niyi · 《易·系辞上》「拟之而后言，议之而后动，拟议以成其变化」）。

---

## 2. 决策

### 2.1 命名正名与意象出处
正式确立中文意象名：**拟议（Niyi）**。
- **出处**：《易·系辞上》「拟之而后言，议之而后动，拟议以成其变化。」
- **含义**：在认知发生实质变更前，先作周详模拟推演，再行审议评估，最终成其健全之认知演化。

### 2.2 领域模型与核心数据结构
定义专职审议结果契约 `DeliberationOutcome`：
```python
@dataclass
class DeliberationOutcome:
    proposal_type: str  # add | update | merge | deprecate
    target_memory_id: str | None
    before_state: dict[str, Any]
    after_patch: dict[str, Any]
    impacted_edges_count: int
    impacted_edge_details: list[dict[str, Any]]
    risk_level: str  # low | medium | high
    deliberation_reason: str
    fenced_reason: str
    confidence_delta: float
    user_id: str | None = None
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

定义未决提案审计结果契约 `AuditReport`：
```python
@dataclass
class AuditReport:
    total_pending: int
    conflicting_pairs: list[dict[str, Any]]
    risk_distribution: dict[str, int]
    audit_summary: str
    fenced_summary: str
    inspected_at: datetime
```

### 2.3 核心引擎 `NiyiEngine`
在 `lantai/cognition/niyi.py` 实现专职引擎：
1. `deliberate_proposal(proposal_type: str, patch_data: dict, target_memory_id: str | None = None)`：
   - 提取目标记忆及其相连的 `MemoryEdge`；
   - 分析更新补丁与类型，当提案为 `deprecate` 且关联重要因果边时，评估风险等级为 `high`；
   - 计算置信度变化预期；
   - 生成结构化审议结论，并使用樊篱 `wrap_as_data` 防护。
2. `audit_pending_proposals(limit: int = 50)`：
   - 全局扫描当前租户内未决提案；
   - 聚类针对同一 `target_memory_id` 的多个提案，标注为潜在竞争互斥提案对（`conflicting_pairs`）；
   - 统计风险分布并输出审计摘要。

### 2.4 多租户行级 ACL 与宁 miss 不脏写
- 强制校验 `tenant_id` 与 `user_id`，非特权用户仅可审议与推演属于自己的记忆与提案；
- 跨租户记忆访问坚决阻断（查询直接过滤为空，返回安全低风险或找不到目标记忆）。

### 2.5 认知上下文切面与 MCP 工具扩容
1. **认知上下文**：`CognitiveContext` 新增 `niyi_digest` 切面，在 `to_prompt()` 中渲染 `## Evolution Proposals & Deliberation (拟议 · {count})`；
2. **MCP 工具池**：注册 `niyi_deliberate` 工具，工具池由 83 项扩充至 **84 项**，保持 1:1 双向映射守卫。

---

## 3. 影响

- 提升认知系统演化的鲁棒性与可预测性，杜绝因果拓扑断裂；
- 为控制台案牍（Andu）与特权治理智能体（持节 Chijie）提供完备的提案预审推演与冲突审计接口；
- 完全向后兼容，不破坏既有 `MemoryProposal` 表结构与生命周期状态机。
