# 基础设施栈逐案：ChromaDB / Qdrant / mem0 / 中文分词器

Type: grilling
Status: resolved
Blocked by: —

## Question

技术栈逐案决定，四个子问题：

1. **向量存储**：ChromaDB 保留还是换 Qdrant？现状是 ChromaDB 内嵌（`PersistentClient`），审计发现向量双重存储（SQLite JSON 列 + ChromaDB）且 cosine 在 Python 中算。是否迁移到 Qdrant 以获得原生距离计算？还是保留 ChromaDB 但消除冗余？
2. **mem0 组件**：是否引入 mem0 的任何组件（如其 memory graph、auto-formatting）？remembrance 已有自己的 gate/evolution/forgetting 链路，引入 mem0 是增益还是冲突？
3. **BM25 中文分词器**：现状 `hybrid_search` 中 BM25 用 `content.split()` 按空格分词——对中文完全无效（"深度学习框架" 被当成一个 token）。需要选型：jieba / pkuseg / HanLP / 其他？还是改用 FTS5 trigram（已有但未集成）替代 BM25？
4. **embedding 模型**：现状 `.env` 用 `BAAI/bge-m3`（1024 维），但 `settings.py` 默认 `text-embedding-3-small`（1536 维）。是否统一为 bge-m3？维度不匹配时如何处理？

**HITL 纪律**：此票据为 grilling 类，必须与用户真人对话完成。

## Answer

四项决议（grilling 2026-08-02 与用户确认）：

### 1. 向量存储 → 保留 ChromaDB，消除冗余

- 删除 `MemoryItem.embedding` JSON 列，只存 ChromaDB
- cosine 用 ChromaDB 返回的 distance，不在 Python 里算
- 不引入 Qdrant（需要外部进程，当前阶段不引入运维负担）

### 2. mem0 组件 → 不引入

remembrance 已有完整的 gate/evolution/forgetting 链路，mem0 的组件会与之冲突而非增益。

### 3. BM25 中文分词器 → jieba

- `pip install jieba`，轻量、成熟、零外部依赖
- FTS5 trigram 作为补充（已有但未集成），不替代 BM25——BM25 有 TF-IDF 加权，trigram 没有
- `hybrid_search` 中 `content.split()` 改为 `jieba.lcut(content)`

### 4. embedding 模型 → 统一为 bge-m3

- `settings.py` 默认 `EMBED_MODEL` 改为 `BAAI/bge-m3`
- 维度问题通过票据 13 删 `VECTOR_DIMENSION` 已解决（ChromaDB 自推断）

ADR：`docs/adr/0004-infra-stack.md`
