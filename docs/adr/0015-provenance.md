# ADR-0015: provenance 提取来源——记忆可溯源

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/provenance/issues/01-provenance.md)

## 背景

腾讯 TencentDB Agent Memory Roadmap v2.0.1：支持自定义提取/召回 prompt，生成的记忆
携带 provenance（哪套 prompt / 哪个模型 / 何时产出）——"Provenance makes 'memory
quality got worse' a traceable question rather than a guess."

兰台现状：候选/提案/记忆都不记录提取来源，记忆质量变差时无法回答"这套记忆是哪套
prompt、哪个模型、什么时候产出的"。调研见
`docs/research/tencentdb-agent-memory-borrow.md`。

## 决策

| 项 | 决策 |
|----|------|
| 数据模型 | `MemoryCandidate` / `MemoryProposal` / `MemoryItem` 各加 `provenance` JSON 列（prompt + model + extracted_at），user_version 5→6 增量迁移（老库零丢失） |
| prompt 标识 | `lantai/core/provenance.py` 常量：extract-v1（LLM 提取/论文）、fastpath-direct（memory 规则直通）、dialogue-fastpath / dialogue-chitchat（对话零 LLM 路径）；prompt 名即版本标识，未来自定义 prompt 时以版本区分效果 |
| 填充点 | 四个候选创建入口统一 `make_provenance(prompt)`：memory_service（LLM + fastpath）、ingestion/dialogue（fastpath / 闲聊 / LLM）、ingest_worker（论文） |
| 链路继承 | `proposer` 把候选 provenance 复制进提案；`promoter` 落库到 MemoryItem——最终记忆可溯源 |
| 可观测 | 记忆概览 `provenance_by_prompt`（按 prompt 分组计数）；待审候选列表 model_dump 自动带出 provenance |
| 零 LLM 路径 | verbatim 直存 / mem:create-skill 不填 provenance（内容 sha256 幂等，来源即用户直存，无需溯源） |

## 理由

- 只加一列 JSON + 继承复制，不动既有读写路径；prompt 名即版本，未来自定义 prompt 零迁移
- 全链路同源：候选（提取时）→ 提案（继承）→ 记忆（落库），任何一条提取类记忆都能
  回答"谁产出的"
- overview 聚合复用已有只读入口，零写放大