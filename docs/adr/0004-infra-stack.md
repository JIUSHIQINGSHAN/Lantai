# ADR-0004: 基础设施栈

**日期**: 2026-08-02
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/aidumem-port/issues/01-infra-stack-per-case.md)

## 决策

| 子问题 | 决策 |
|--------|------|
| 向量存储 | 保留 ChromaDB，删除 MemoryItem.embedding JSON 列，cosine 用 ChromaDB distance |
| mem0 组件 | 不引入 |
| BM25 中文分词 | jieba（`content.split()` → `jieba.lcut()`），FTS5 trigram 补充不替代 |
| embedding 模型 | 统一 BAAI/bge-m3，改 settings 默认值 |

## 理由

- ChromaDB 内嵌零外部进程，Qdrant 需要额外运维
- mem0 与现有 gate/evolution/forgetting 链路冲突
- jieba 轻量成熟，`pip install jieba` 零外部依赖
- bge-m3 已在 .env 中使用，维度问题通过删 VECTOR_DIMENSION 解决

## 相关

- [ADR-0002](0002-zero-hardcoding.md) — 删 VECTOR_DIMENSION
- [票据 01](../../.scratch/aidumem-port/issues/01-infra-stack-per-case.md)
