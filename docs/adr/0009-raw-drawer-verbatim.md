# ADR-0009: Raw Drawer 原文直存（verbatim 记忆）

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [发展方向调研](docs/research/direction-research-report.md)「立即做」+ v0.5 优化方案 P0-1

## 决策

新增 `POST /add/raw` 原文直存通道，要点：

1. **零 LLM 直写**：内容只做 embedding + FTS5 索引，直接创建 `MemoryItem(memory_type="verbatim")`，**不走提取/闸门/演化流水线**（长代码/日志/配置经 LLM 提取会有细节损失且消耗 token）。
2. **幂等去重**：内容 sha256 作 `MemoryItem.key`（key 字段承载，不新增列），同内容再次写入返回既有 memory_id（dedup=true），不重复索引。
3. **检索自动命中**：verbatim 走现有四路混合检索——向量命中 + FTS5 trigram 子串容错（天然适配原文片段），无需检索侧改动。
4. **衰减语义**：`decay_class="semantic"`（慢衰减）；`tier=long_term`；`confidence=1.0`（原文即事实）。默认 lane 取 `RAW_MEMORY_DEFAULT_LANE`（settings，零硬编码）。

## 理由

- **需求真实**：编码 agent 场景（代码/日志/配置原文）是 2026 记忆生态增长最快的细分（agentmemory / claude-code-memory 类目爆发）；「简单存储够用」论据（Letta Filesystem 在 LoCoMo 达 74%）侧面支持 verbatim 直存。
- **符合既有铁律**：宁 miss 不脏写——原文直存不提炼、不改写，天然无「脏写」风险；去重幂等不产生垃圾。
- **成本最低**：1 个 schema + 1 个 service 函数 + 1 个路由 + 1 个测试文件，复用 `index_memory_item` / `sync_fts`。
- **零新依赖**：复用 SQLite FTS5 + ChromaDB。

## 影响面

- `lantai/models/schemas.py`：新增 `RawMemoryReq`（metadata 与 AddMemoryReq 共用有界校验器）。
- `lantai/services/memory_service.py`：`add_raw_memory`（幂等去重 + embedding + 双索引）。
- `lantai/api/routes_memory.py`：`POST /add/raw`。
- `lantai/core/settings.py`：`RAW_MEMORY_DEFAULT_LANE`。
- **明确不做**：verbatim 不走提案/回滚链（幂等可重写，无历史版本语义）；不新增存储表。

## 已知限制

- verbatim 记忆无 checkpoints，回滚 API 对它不适用（重复写入即「覆盖」，幂等去重后需先删除）。
- 极端大段内容（>200K 字符）由 `RawMemoryReq.content` 上限截断。
