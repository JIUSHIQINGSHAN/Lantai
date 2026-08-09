# 01 - Dialogue Ingest 对话写通道

Status: ready-for-agent
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
