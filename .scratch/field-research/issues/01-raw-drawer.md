# 01 - Raw Drawer 原文直存（verbatim 记忆）

Status: resolved
Type: task
Source: docs/research/direction-research-report.md「立即做」+ v0.5 优化方案 P0-1

## 目标

`POST /add/raw`：内容直入 FTS5 + ChromaDB 向量，只 embedding 不 LLM；不走提取/闸门/演化流水线。
`MemoryItem.memory_type="verbatim"`；去重用内容 sha256（key 字段承载，幂等）；检索自动命中。

## 验收

1. add_raw_memory 核心函数：真实内存 SQLite + FTS 写入 → memory_type=verbatim → 检索可命中（不 mock 冒烟）
2. 重复内容幂等：返回同一 memory_id（dedup=true）
3. 零 LLM 调用断言（不 mock 提取器，直接断言未走提取链）
4. 新阈值进 settings（RAW_MEMORY_DEFAULT_LANE），无硬编码

## 相关文件

lantai/services/memory_service.py、lantai/api/routes_memory.py、lantai/models/schemas.py、
lantai/core/settings.py、tests/test_raw_memory.py（新）、docs/adr/0009-raw-drawer-verbatim.md（新）
