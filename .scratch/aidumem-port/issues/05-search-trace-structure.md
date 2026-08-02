# `/search_trace` 全链路追踪的输出结构与 overhead 上限

Type: prototype
Status: resolved
Blocked by: —

## Question

aiduMEM 有 `/search_trace` 端点，展示检索全链路：意图分类 → 向量检索 → BM25 → 衰减 → rerank 的每一步耗时和中间结果。

需要决定：

1. **输出结构**：trace 的 JSON 形状？每步记录什么（耗时、候选数、分数分布）？
2. **overhead 上限**：trace 本身不能太重——采样率？全量还是按需开启？额外延迟上限？
3. **与 `/search` 的关系**：trace 是 `/search` 的参数（`?trace=true`）还是独立端点？

**原型即弃**：用 /prototype 技能搭一次性原型，验证输出结构和性能开销。

## Answer

设计决议（grilling 2026-08-02 与用户确认，原型实现延至 Phase 3-6）：

### 1. 输出结构

数组，每步一个对象：
```json
{"trace": [
  {"step": "intent", "elapsed_ms": 120, "candidate_count": null, "score_range": null},
  {"step": "vector_search", "elapsed_ms": 45, "candidate_count": 20, "score_range": [0.3, 0.9]},
  {"step": "bm25", "elapsed_ms": 8, "candidate_count": 20, "score_range": [0.0, 3.2]},
  {"step": "decay_filter", "elapsed_ms": 1, "candidate_count": 18, "score_range": null},
  {"step": "rerank", "elapsed_ms": 280, "candidate_count": 5, "score_range": [0.1, 0.95]},
  {"step": "final", "elapsed_ms": 454, "candidate_count": 5, "score_range": [0.1, 0.95]}
]}
```

### 2. overhead → 按需开启，不采样

- `?trace=true` 参数开启
- trace 只记耗时和计数，不记中间结果内容，overhead < 1ms

### 3. 与 /search 关系 → 参数

- `/search` 加 `trace=true` 参数，返回体加 `trace` 字段
- 不开独立端点（减少 API 表面积）
