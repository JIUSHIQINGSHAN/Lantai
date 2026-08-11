# ADR-0018: 冷启动导入——历史会话 JSONL 批量导入，保留原始时间戳

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/import/issues/01-cold-start-import.md)

## 背景

腾讯 TencentDB Agent Memory：
- L0 会话记录：原始对话消息写 JSONL（每行一条消息，sessionKey + role + content +
  timestamp），独立于系统会话文件、格式自控；
- v2.0.1 时间戳修正：「导入会话保留原始时间戳——导入历史对话后时间线不再被压平
  到导入时刻」（配套面板按时间范围过滤记忆）。

兰台现状：所有入口（dialogue/论文/RSS）的候选/记忆 created_at 都是「写入时刻」，
冷启动导入历史对话时时间线会被压平——digest 当日统计、overview、遗忘曲线全部
按导入日而非原始发生日计数，历史会话导入价值大打折扣。

## 决策

| 项 | 决策 |
|----|------|
| 输入格式 | 腾讯 L0 同款 JSONL：`{"role": "user"\|"assistant", "content": "...", "timestamp": <epoch ms/s 或 ISO>[, "session": "id"]}` |
| 解析 | `parse_session_line`（纯函数）：非法行返回 None 计数，不抛不拖停整批；时间戳统一归一化为 naive UTC（含时区偏移换算） |
| 摄取 | 只把 user 消息喂既有 `ingest_dialogue` 链（fastpath 直通 / LLM 提取建候选 / 闲聊入待审），assistant 行跳过——语义与对话写通道完全一致（宁 miss 不脏写） |
| 时间戳保留 | `ingest_dialogue(created_at=...)` 透传：RawDocument.fetched_at / MemoryCandidate.created_at = 消息原始时间；provenance.prompt = `dialogue-session-import` |
| 演化链继承 | promoter 新增记忆时，import provenance 候选的 created_at 继承到 MemoryItem.created_at（时间线不压平）；非 import 路径行为不变 |
| 入口 | 对话链通道：CLI `scripts/run_import.py`（--file / --dry-run / --json）+ service 入口，不加 MCP；verbatim 原文直存另走 REST `POST /import/jsonl`（票据 07，零 LLM 快速灌库）——两通道互补：原文直存与提取/闸门/待审各得其所 |
| 防护 | `IMPORT_MAX_LINES=5000` 单次上限；dry_run 预览零副作用 |

## 理由

- 复用对话摄取链（提取/闸门/候选/待审）而非新造直存路径——历史会话与日常对话
  同一条生产线，行为一致、维护成本低
- 时间戳继承走 provenance 标记（prompt 名即版本）而非加新列：既有链路零迁移，
  prompt 名可溯源「这套记忆来自历史导入」
- JSONL 格式与腾讯 L0 对齐：未来可以直接镜像/迁移腾讯格式的会话文件

## 代价

- 导入的 fastpath/提取候选仍需 evolve 流程落库（批量导入不即时成为记忆，符合
  「宁 miss 不脏写」）
- 时间戳继承只对 import provenance 生效（对照组验证非导入路径不被覆盖）

## 修订（2026-08-12）

并行 verbatim 直存通道（票据 07：`lantai/services/import_service.py` + `POST /import/jsonl` + `scripts/import_jsonl.py`，零 LLM、sha256 幂等、保留原始时间戳）与本对话摄取链通道并存互补：需要原文直存快速灌库走 REST/CLI，需要走提取/闸门/待审生产线走 `scripts/run_import.py`。