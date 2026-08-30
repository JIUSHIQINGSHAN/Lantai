# ADR-0028: 拾遗——零召回根因治理与检索韧性降级

**日期**: 2026-08-30
**状态**: Accepted
**决策者**: 大哥
**来源**: 生产库只读审计：`recall_health.py` 显示 100% 零召回率，`retrieval_event` 连续 22 次检索 0 结果；排查证实闸门误拦截、API Key 失效、`hybrid_search` 缺乏异常防护三层叠加。

---

## 背景

经 `docs/memory-quality/zero-recall-diagnosis-2026-08-30.md` 确证，生产环境 100% 零召回由三层相互交织的根因导致：

1. **闸门过严阻断（主因之一）**：`prefilter.py::relevance_check` 要求非预置句型必须 `len > 15`，且未内置 "大哥" 等核心自指，导致常见短查询（如 "大哥电脑配置"、"什么是事件驱动架构"）全部被判定为 `needs_memory=False`，在 `/search` 入口直接截断为 `[]`。
2. **嵌入 API 异常（主因之二）**：`.env` 中的外部 Embedding Token 报 401 Unauthorized。
3. **降级通路阻断（韧性缺陷）**：`hybrid_search` 原本设计了 `_keyword_fallback`（FTS5 + BM25 本地双路兜底），但第 139 行的 `embed([query])` 未置于 `try...except` 保护中，API 异常直接抛出未捕获异常，导致降级逻辑从不生效。

---

## 决策

确立「拾遗」（Shiyi）多级检索韧性治理机制：

| 层面 | 决策 | 规范要求 |
|---|---|---|
| **命名** | 「拾遗」（Shiyi） | 取自唐代官职「拾遗」及捡拾遗漏之义，见 [ADR-0013](0013-naming-system.md) 候选池，已登记 `CONTEXT.md` |
| **混合检索降级** | `hybrid_search` 嵌入保护 | `embed([query])` 用 `try...except Exception` 保护；网络中断/鉴权失败/超时均记 warning，平滑降级至 `_keyword_fallback`（本地 FTS5 + BM25） |
| **启发式闸门校准** | `prefilter.py` 阈值优化 | 自指词表收录 "大哥"；汉字 `>=3` 且含实质内容词（如配置/架构/代码/诗词/怎么/什么是）判定为需要记忆，不再武断以 15 字卡死 |
| **显式接口透传** | `/search` 路由显式放行 | `SearchReq` 支持 `force: bool = False`；显式调用 `/search` 若携带明确查询意图，不因闸门无标记而完全丢弃 |

---

## 理由

1. **高可用性（宁降级不挂死）**：外部 LLM/Embedding API 随时可能因欠费、Token 过期或网络瞬断而失效。兰台本地维护着完整的 SQLite、BM25 和 FTS5 trigram 索引，在外部不可用时降级为本地关键词检索，可保持 80%+ 的基本可用性。
2. **名实相副（实词短句也是记忆）**：用户在检索时往往习惯输入 4–8 个字的简短词组（如 "天选三显卡"、"飞书卡片配置"）。原 15 字符硬门槛违背用户习惯。
3. **架构正交（闸门与检索解耦）**：闸门的主要职责是避免聊天无意义轮次（如 "好的"、"收到"）盲目检索向量库；对于有实质词的查询，应当将决策权交给检索引擎的分数阈值，而非在前置闸门一刀切。

---

## 影响

- `lantai/retrieval/hybrid.py`：`embed([query])` 异常保护 + trace 信息记录降级状态。
- `lantai/gate/prefilter.py`：自指与实词正则增强。
- `lantai/api/routes_search.py`：`SearchReq.force` 支持。
- 测试：`tests/test_zero_recall_remediation.py`（不 mock 冒烟：API 401 模拟真实降级 + 真实 FTS 召回）。

---

## 相关

- [ADR-0013](0013-naming-system.md) — 拾遗命名登记
- [CONTEXT.md](../../CONTEXT.md) — 拾遗词汇定义
- [诊断报告](../memory-quality/zero-recall-diagnosis-2026-08-30.md)
