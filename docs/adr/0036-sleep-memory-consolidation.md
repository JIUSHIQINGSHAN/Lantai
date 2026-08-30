# ADR-0036: 沉潜——闲时夜梦记忆沉淀与折叠压缩机制

**日期**: 2026-08-31
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴认知神经科学中人类大脑睡眠记忆巩固（Sleep Memory Consolidation / Synaptic Pruning）与自适应图神经网络聚类思想。

---

## 背景

长期对话运行后，记忆库会积累大量碎片化事实：
1. **碎片化冗余**：例如用户多次提及“今天喝了龙井”、“昨天也喝了龙井”、“下午泡了龙井茶”，产生多条相似度极高的碎片记忆；
2. **检索性能与精度退化**：大量碎片记忆抢占 Top-K 检索名额，造成上下文冗余；
3. **噪音累积**：深度衰减的过时事实长期驻留。

---

## 决策

引入**「沉潜」（Chenqian）** 闲时夜梦沉淀与折叠压缩机制：

### 核心机制

1. **同类碎片聚类发现（Cluster Discovery）**：
   - 按 `domain`、`lane` 以及相似主题/语义距离对活跃记忆进行无监督分组（每组 $\ge 3$ 条碎片）；
2. **概念综合提纯（Synthesis & Folding）**：
   - 调用 LLM 提纯生成 1 条高阶概括性主记忆（例如：*“大哥日常有饮用龙井茶的稳定偏好”*）；
   - 将原始碎片记忆关联为子记忆（`source_ids` 挂载），子记忆状态标记为 `consolidated`（折叠归档，主检索不重复召回，但保留完整历史追溯）；
3. **边缘突触休眠修剪（Synaptic Pruning）**：
   - 对衰减分极低（`decay_score < 0.05`）且无长期采纳记录的陈旧碎片记忆，自动转入 `archived`（休眠归档）；
4. **安全与快照（宁 miss 不脏写）**：
   - 每次折叠前生成快照，支持秒级撤销；LLM 解析失败则保持原样不变。

---

## 影响

- 核心服务：`lantai/services/consolidation_service.py`
- 调度器：在 `scheduler.py` 中注册每日凌晨/闲时沉淀任务
- 接口：REST `POST /evolution/consolidate` 与 MCP `memory_consolidate` / `consolidation_report`
- 测试：`tests/test_consolidation.py`（真实不 mock 冒烟单测）

---

## 相关

- [ADR-0013](0013-naming-system.md) — 沉潜命名登记
- [CONTEXT.md](../../CONTEXT.md) — 沉潜词汇定义
