# 01 - Dialogue Ingest 对话写通道

Status: resolved
Type: task
Blocked by: (none)

## 目标

新增对话写入入口：对话文本 → 现有提取流水线（extract→gate 分层），
"记住"标记直通 Fastpath。复用 rawdocument→memorycandidate 链，不新建存储。

## 范围

- 新模块 `remembrance/ingestion/dialogue.py`：
  - `ingest_dialogue(text, *, user_id, source)`：建 rawdocument → extract_candidate
    → gate 分层（分 lane 自动提取 vs 闲聊 reject 入队）→ Fastpath 标记直通
  - 降级：LLM 提取失败（上游 502）→ 兜底候选进 pending_review，不丢数据
- MCP 工具 `add_dialogue` + REST `POST /dialogue`（薄路由，业务在 service）
- 复用 gate 7 类启发式与 fastpath 白名单，不改其语义

## 验收

1. 对话含偏好/事实 → 生成候选（lane 正确）
2. 闲聊内容 → 候选进 pending_review（不静默丢弃、不落库为记忆）
3. "记住：xxx" 标记 → Fastpath 直通
4. LLM 提取异常 → 兜底候选入队，不抛错
5. `ingest_dialogue` 有不 mock 冒烟测试（真实内存 SQLite）

## 相关文件

remembrance/ingestion/dialogue.py（新）、remembrance/api/routes_dialogue.py（新）、
scripts/mcp_server.py、tests/test_dialogue_ingest.py（新）

## Answer（2026-08-09 已实现，全量 422 测试全绿）

实现内容：
- 新模块 `remembrance/ingestion/dialogue.py`：
  - `ingest_dialogue(text, *, user_id, source)`：fastpath 直通 → 闲聊判断 → LLM 提取，
    全程复用 rawdocument→memorycandidate 链（content_hash 去重复用 doc），不新建存储；
  - `_guess_lane`：对话入口 lane 预判（preference/fact/general，宽松 search，
    与 fastpath 整段 match 语义互补不改动）；
  - `_is_chitchat`：过短（DIALOGUE_MIN_CHARS）或纯社交结束语（复用 prefilter 模式）；
  - 降级：LLM 提取失败（extract_candidate 自带 fallback confidence 0.3）
    或低置信度 → 兜底候选进 pending_review（enqueue_rejected），不抛错不丢数据。
- settings 新增：`DIALOGUE_ENABLED`、`DIALOGUE_MIN_CHARS=8`、
  `DIALOGUE_MIN_EXTRACTOR_CONF=0.55`（对话通道专用阈值——不受 .env 覆盖
  GATE_MIN_EXTRACTOR_CONF=0.25 影响，避免 fallback 被当成可接受置信度）。
- REST `POST /dialogue`（routes_dialogue.py，注册 protected_routers）；
  MCP `add_dialogue`（scripts/mcp_server.py，tools 共 7 个）。
- 触发源（Hermes 会话结束钩子 / state.db 扫描）留待 Ticket 05 验证接入。

验收对照：
1. ✅ 偏好/事实 → 候选 lane 正确（fastpath lane=fact/general；LLM 路径 lane=preference/general）
2. ✅ 闲聊 → 候选进 pending_review（建 rawdocument 可追溯，不落库为记忆）
3. ✅ "记住：X" → Fastpath 直通（绕过 LLM 提取，测试用 side_effect 断言未触发）
4. ✅ LLM 提取异常 → 兜底候选入队（RuntimeError 注入测试，不抛错）
5. ✅ ingest_dialogue 不 mock 冒烟测试：tests/test_dialogue_ingest.py 11 例
   （真实内存 SQLite，仅 mock 外部 LLM chat_json）+ test_mcp.py 2 例

实现说明/偏差：
- 新增 DIALOGUE_MIN_EXTRACTOR_CONF 专用阈值（ticket 未列，为满足"提取失败入队"语义
  且隔离 .env 对 GATE_* 的覆盖；已进 settings 零硬编码）。
- 闲聊路径也建 rawdocument + 候选（验收 2 要求"候选进 pending_review"），
  用户可在待审队列里看到并归档，可追溯原文。
