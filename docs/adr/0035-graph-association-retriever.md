# ADR-0035: 贯珠——基于实体图谱拓扑的二度语义联想与多跳召回机制

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 借鉴 Cognee 实体知识图谱架构；突破传统单步检索字面匹配局限，实现常识与跨实体多跳联想。

---

## 背景

传统的混合检索（向量 + BM25）在面对复杂上下文推理时存在局限：
1. 若用户查询仅提及“华硕天选三”，检索仅能匹配到包含“天选三”的段落；
2. 无法自动联想到通过图关系（`MemoryEdge`）相连的硬件参数（如“RTX 3050”、“16G 内存”），导致 Agent 回答不全面。

---

## 决策

引入**「贯珠」（Guanzhu）** 图谱二度语义联想与多跳召回机制：

### 核心机制

1. **图拓扑广度优先遍历 (BFS)**：
   - 从初筛 Top 命中的种子记忆（`seed_memory_ids`）出发；
   - 沿 `MemoryEdge`（双向：`source_memory_id` <-> `target_memory_id`）展开 1~2 步（hops）关系扩散；
   - 过滤环路与已访问集合，仅保留满足最低置信度（`min_edge_conf`，默认 0.5）的高质量边。
2. **多跳拓扑路径溯源**：
   - 联想出的每条记忆均包含跳数（`hop`）、关联关系（`relation`）与前驱记忆 ID（`via_memory_id`），做到 100% 可解释与可审计。
3. **接口面开放**：
   - REST：`POST /search/graph_expand`
   - MCP：`graph_expand_search`

---

## 理由

1. **名实相副**：「贯珠」出自《汉书·景十三王传》“如贯珠焉”，指事物如串珠般紧密相连，取知识图谱实体二度跳跃联想之意。
2. **深度上下文感知**：使兰台具备类似人类大脑的“顺藤摸瓜”常识联想能力。

---

## 影响

- 检索：新增 `lantai/retrieval/graph_retriever.py`。
- 路由与工具：更新 `lantai/api/routes_search.py`，新增 MCP 工具 `graph_expand_search`（工具总数扩充至 51）。
- 测试：`tests/test_graph_retriever.py`（真实不 mock 冒烟单测）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 贯珠命名登记
- [CONTEXT.md](../../CONTEXT.md) — 贯珠词汇定义
