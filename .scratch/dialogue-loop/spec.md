# Spec: 对话闭环（Dialogue Loop）v0.5

Status: ready-for-agent
Type: spec

## 背景

用户的实际使用模式：Hermes 自动注入检索（420 次），写入极少（20 次 add），
feedback 0 次，记忆库仅 4 条。核心矛盾：**系统在查询、不在积累**。
目标：让记忆从对话中自动长出来，且积累过程可见可审。

## 范围

五个组件：
1. Dialogue Ingest（对话写通道）—— 核心
2. Candidate Review Queue（候选可见队列）
3. Daily Digest（每日盘点报告）
4. Search Transparency（检索透明）
5. Hermes Session Hook 验证（自动触发源）

## 非目标（明确不做）

- 不做 aiduMEM 功能对齐（联邦/代码图谱/树状记忆等，降级 backlog）
- 不新建存储层（复用 rawdocument→candidate→proposal→memoryitem 链）
- 不引入 mem0/Qdrant

## 数据模型变更

- `memorycandidate` 加字段：`review_due_at DATETIME NULL`（TTL 截止）
- `memorycandidate.status` 扩展值：`pending_review`（reject 改为入队）
- 新表：无（digest 报告为文件，不建表）

## 接口契约

REST（注册进 protected_routers）：
- `POST /dialogue`               对话文本写入（触发提取）
- `GET /candidates/pending`      待审候选列表
- `POST /candidates/{id}/review` 审核（approve/reject）
- `GET /digest/today`            当日盘点报告

MCP 新增工具：
- `add_dialogue` / `candidates_pending` / `candidate_review` / `get_digest`

## settings 新阈值（零硬编码）

- `DIALOGUE_*`：分 lane 开关、min 长度
- `CANDIDATE_TTL_DAYS = 7`
- `DIGEST_PATH`（默认 docs/memory-digest/）

## 验收标准

1. 对话进系统：有价值内容自动进候选/直通，闲聊不落库；
2. 被拒候选可见可审，TTL 自动归档；
3. 每日 digest 生成，Hermes 早晨可读；
4. 检索注入带"依据"；
5. 核心函数有不 mock 冒烟测试；全量测试全绿；无新硬编码。

## 风险

- ~~Hermes 插件会话结束事件是否存在~~（ticket 05 已验证：**存在**——`on_session_end`
  每轮对话结束触发，桌面版与 CLI 通用；payload 无消息文本，需插件自缓冲
  pre_llm_call 的 user_message，on_session_end 时 flush 给 ingest_dialogue；
  备选 state.db 只读扫描 schema 已探明（sessions/messages 表 + WAL 安全））
- 触发源实现顺序：① 插件 on_session_end 缓冲 flush（实时首选）→ ② cron 每日
  state.db 只读扫描（兜底，隔日感知）
- 对话提炼 LLM 成本（分 lane + Fastpath 控制，降级兜底不丢）
