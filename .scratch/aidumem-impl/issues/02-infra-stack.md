# 02 — 基础设施栈

**What to build:** 删除 `MemoryItem.embedding` JSON 列消除冗余（只存 ChromaDB）；`hybrid_search` 中 `content.split()` 改为 `jieba.lcut(content)`；`settings.EMBED_MODEL` 默认值改为 `BAAI/bge-m3`。不引入 mem0 或 Qdrant。

**Blocked by:** 01 — P0 修复 + 零硬编码

**Status:** ready-for-agent

- [ ] `MemoryItem.embedding` JSON 列删除，向量只存 ChromaDB
- [ ] cosine 用 ChromaDB 返回的 distance，不在 Python 里算
- [ ] `hybrid_search` 中 BM25 分词用 `jieba.lcut()`，不用 `content.split()`
- [ ] `settings.EMBED_MODEL` 默认值为 `BAAI/bge-m3`
- [ ] 所有现有测试通过
