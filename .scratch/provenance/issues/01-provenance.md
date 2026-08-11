# 01 - provenance 提取来源（哪套 prompt / 哪个模型 / 何时产出）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory Roadmap v2.0.1（自定义 prompt + provenance）：生成的记忆
携带「哪套 prompt / 哪个模型 / 何时产出」，让"记忆质量变差"成为可溯源问题而非猜测。
兰台落点：candidate / proposal 记录提取 prompt 版本，最终 MemoryItem 可溯源。

## 范围

- `lantai/core/provenance.py`：make_provenance（prompt + model + extracted_at）与
  prompt 名常量（extract-v1 / fastpath-direct / dialogue-fastpath / dialogue-chitchat）
- 三表加 `provenance` JSON 列（user_version 5→6 迁移）：memorycandidate / memoryproposal / memoryitem
- 提取入口填充：memory_service（LLM 提取 + fastpath 直通）、ingestion/dialogue
  （fastpath / 闲聊兜底 / LLM）、ingest_worker（论文提取）
- 链路继承：proposer 复制候选来源到提案；promoter 落库到 MemoryItem
- 可观测：记忆概览按 prompt 分布（overview.provenance_by_prompt）；
  待审候选列表 model_dump 自动带出 provenance

## 验收

1. `make_provenance` 纯函数有不 mock 冒烟测试
2. 四个提取入口的候选 provenance.prompt 正确
3. candidate → proposal → MemoryItem 全链路同源（可溯源到 prompt）
4. v5→v6 迁移幂等、老库零丢失
5. overview 按 prompt 分布计数（不 mock 聚合逻辑）
6. 全量 pytest 绿

## 相关文件

docs/adr/0015-provenance.md（本票产物）、lantai/core/provenance.py、
lantai/models/tables.py、lantai/storage/db.py、lantai/services/memory_service.py、
lantai/ingestion/dialogue.py、lantai/workers/ingest_worker.py、
lantai/evolution/proposer.py、lantai/evolution/promoter.py、lantai/ops/overview.py、
tests/test_provenance.py、docs/research/tencentdb-agent-memory-borrow.md

## Comments

## Answer（2026-08-11 已实现，test_provenance.py 8/8 + 全量回归绿）

- make_provenance 返回 {prompt, model, extracted_at}；prompt 名即版本标识（extract-v1 等）
- 提取入口统一填充：LLM 提取/论文 → extract-v1；memory fastpath → fastpath-direct；
  dialogue fastpath → dialogue-fastpath；闲聊兜底 → dialogue-chitchat
- proposer 把候选 provenance 复制进提案；promoter 落库到 MemoryItem——最终记忆可回答
  "这套记忆是谁产出的"
- overview 新增 provenance_by_prompt（按 prompt 分组计数）；candidates_pending 列表
  model_dump 自动带出
- 测试：make_provenance 不 mock 冒烟；四入口 + 全链路 + v5→v6 迁移 + overview 聚合
  8 例；全量 pytest 通过