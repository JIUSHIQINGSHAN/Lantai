# Reranker 集成 Spec

**日期**: 2026-07-30
**状态**: Draft
**决策者**: 大哥

---

## 背景

v0.2.0 骨架阶段完成后，检索精度是下一个瓶颈。当前混合检索（0.6 向量 + 0.3 BM25 + 0.1 衰减）在测试集上表现粗糙，需要 Cross-Encoder Reranker 做精排。

---

## 8 个关键决策

| # | 问题 | 答案 | 理由 |
|---|------|------|------|
| 1 | 优先级 | A（Reranker） | 检索精度是核心差异化的关键 |
| 2 | 部署方式 | B（远程 API） | 硅基流已支持，无需本地 GPU |
| 3 | 架构位置 | B（混合检索之后） | 标准两阶段：粗排 → 精排 |
| 4 | 候选集大小 | D（动态调整） | 根据 query 复杂度自适应 |
| 5 | 动态规则 | C（意图分类） | 区分 fact lookup vs exploratory search |
| 6 | 意图分类实现 | A（每次多调一次 LLM） | 分类准，延迟可接受 |
| 7 | 降级策略 | B（重试一次） | 1s 后重试，还不行就跳过 |
| 8 | API 地址 | A | `https://api.siliconflow.cn/v1/rerank` |

---

## 架构

```
POST /search
  │
  ├─ 1. 意图分类（LLM 多调一次）
  │     ├─ "fact lookup"  → candidate_n = 10
  │     ├─ "procedural"   → candidate_n = 15
  │     └─ "exploratory" → candidate_n = 20
  │
  ├─ 2. 混合检索（现有逻辑，返回 candidate_n * 2 条）
  │
  ├─ 3. Reranker（硅基流 API，对候选集重排）
  │     ├─ 成功 → 返回 top_k 条
  │     └─ 失败 → 重试 1 次（+1s）→ 还失败则跳过，返回混合检索结果
  │
  └─ 4. 返回结果
```

---

## API 调用示例

```python
POST https://api.siliconflow.cn/v1/rerank
Authorization: Bearer {OPENAI_API_KEY}

{
  "model": "BAAI/bge-reranker-v2-m3",
  "query": "怎么做记忆迭代",
  "documents": ["doc1", "doc2", "doc3", ...],
  "top_k": 5,
  "return_documents": true
}

Response:
{
  "id": "rerank-xxx",
  "results": [
    {"index": 2, "score": 0.95, "document": "doc3"},
    {"index": 0, "score": 0.87, "document": "doc1"},
    ...
  ]
}
```

---

## 新增依赖

- `requests`（已有，不需要新增）

---

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `remembrance/retrieval/hybrid.py` | 新增 rerank 阶段 |
| `remembrance/retrieval/intent.py` | 新增意图分类 |
| `remembrance/core/settings.py` | 新增 `RERANKER_*` 配置 |
| `remembrance/models/schemas.py` | SearchReq 新增 `use_rerank` 参数 |
| `tests/test_reranker.py` | 新增 reranker 单元测试 |

---

## 验收标准

- [ ] `/search` 支持 `use_rerank=true/false`
- [ ] 意图分类正确识别 fact/procedural/exploratory
- [ ] Reranker 返回结果按 score 降序
- [ ] Reranker 失败时自动重试一次
- [ ] 重试失败后降级为混合检索结果
- [ ] 所有现有测试继续通过
- [ ] 新增测试覆盖：rerank 路径、降级路径、意图分类路径

---

## 风险

1. **LLM 意图分类延迟**：每次搜索多 200-500ms。可后续优化为规则匹配。
2. **硅基流限流**：429 时自动重试，但重试也可能被限流。需要客户端侧限流。
3. **候选集大小**：`candidate_n * 2` 条传给 reranker，如果 candidate_n=20 就是 40 条，可能有性能问题。需要实测。
