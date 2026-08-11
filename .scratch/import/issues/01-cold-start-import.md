# 01 - 冷启动导入：历史会话 JSONL 批量导入，保留原始时间戳

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴腾讯 TencentDB Agent Memory L0 会话记录（JSONL：每行一条消息，
sessionKey + role + content + timestamp）与 v2.0.1 时间戳修正（「导入会话保留
原始时间戳（含 JSONL 镜像）——导入历史对话后时间线不再被压平到导入时刻」）：
批量导入历史会话 JSONL 走既有对话摄取链，落库时间 = 消息原始时间戳。

## 范围

- `lantai/ingestion/import_service.py`：`normalize_timestamp`（epoch 毫秒/秒/ISO →
  naive UTC，纯函数）/ `parse_session_line`（单行解析，纯函数）/ `import_session_jsonl`
  （批量导入：user 消息喂摄取链，assistant/坏行跳过计数，dry_run 预览）
- `lantai/ingestion/dialogue.py`：`ingest_dialogue(created_at=...)` 透传——RawDocument
  fetched_at / MemoryCandidate created_at = 原始时间戳，provenance.prompt =
  dialogue-session-import
- `lantai/evolution/promoter.py`：新增记忆时，import provenance 候选的 created_at
  继承到 MemoryItem（时间线不压平；非导入路径行为不变）
- `lantai/core/provenance.py`：新增 `PROVENANCE_PROMPT_DIALOGUE_IMPORT`
- `lantai/core/settings.py`：`IMPORT_MAX_LINES=5000`（防误喂超大文件）
- `scripts/run_import.py`：CLI（--file / --dry-run / --json / --limit）
- 文档：ADR-0018、借鉴报告落地顺序 8、CONTEXT 词汇表、CHANGELOG

## 验收

1. normalize_timestamp / parse_session_line 纯函数不 mock 冒烟
2. 真实 SQLite + tmp_path：user 消息落候选 created_at=原始时间戳；assistant/坏行跳过
3. dry_run 零写库；单行失败不拖停整批
4. 演化链：import 候选 → MemoryItem.created_at = 原始时间戳；非 import 路径不覆盖
5. 不新增 MCP/REST（批量离线操作用 CLI；工具数保持 21）

## 相关文件

lantai/ingestion/import_service.py、lantai/ingestion/dialogue.py、
lantai/evolution/promoter.py、lantai/core/provenance.py、lantai/core/settings.py、
scripts/run_import.py、tests/test_import.py、docs/adr/0018-import.md、
docs/research/tencentdb-agent-memory-borrow.md

## Answer（2026-08-11 已实现，test_import.py 9/9 + 全量回归绿）

实现内容：
- JSONL 格式与腾讯 L0 同款：{"role", "content", "timestamp"(epoch ms/s 或 ISO)},
  可选 session；只导入 user 消息（assistant 跳过），单行解析失败只计数不拖停。
- 时间戳保留贯穿全链路：RawDocument.fetched_at / MemoryCandidate.created_at =
  消息时间，provenance.prompt = dialogue-session-import，promoter 据此把候选
  created_at 继承到 MemoryItem.created_at（对照组：非导入路径不覆盖）。
- CLI：--dry-run 预览 / 实际导入 / --json；行数与状态分布（fastpath/new/
  pending_review）输出。
- 入口决策：导入是冷启动一次性批量离线操作 → 只做 CLI + service 入口，
  不加 MCP/REST（避免永久扩大 agent 工具面）。

验收对照：
1. ✅ normalize_timestamp / parse_session_line 冒烟（含时区偏移换算）
2. ✅ 候选 created_at=原始时间戳 + assistant/坏行计数
3. ✅ dry_run 零写库 + 坏行不拖停（2 条好行 + 1 坏行 → imported=2）
4. ✅ 演化链继承 + 非导入对照组不覆盖
5. ✅ 工具数保持 21（未动 MCP）

补充（2026-08-12）：并行 verbatim 直存通道（票据 07，`POST /import/jsonl`）与本通道并存互补——原文直存零 LLM 快速灌库，本通道走提取/闸门/待审；两通道均保留原始时间戳。