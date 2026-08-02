# 02 — 基础设施栈

**What to build:** 删除 `MemoryItem.embedding` JSON 列消除冗余（只存 ChromaDB）；`hybrid_search` 中 `content.split()` 改为 `jieba.lcut(content)`；`settings.EMBED_MODEL` 默认值改为 `BAAI/bge-m3`。不引入 mem0 或 Qdrant。

**Blocked by:** 01 — P0 修复 + 零硬编码

**Status:** resolved

- [x] `MemoryItem.embedding` JSON 列删除，向量只存 ChromaDB
- [x] cosine 用 ChromaDB 返回的 distance（cosine space），不在 Python 里算
- [x] `hybrid_search` 中 BM25 分词用 `jieba.lcut()`，不用 `content.split()`
- [x] `settings.EMBED_MODEL` 默认值为 `BAAI/bge-m3`
- [x] 5 个新测试全绿，68/74 全量测试通过（6 个预存 bug 非 T02 引入）
