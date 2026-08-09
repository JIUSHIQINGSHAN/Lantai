# 05 - Hermes 会话结束钩子验证

Status: resolved
Type: research
Blocked by: (none)

## 目标

验证 Hermes 是否有会话结束事件可挂接"对话自动写入"，为 01 提供自动触发源。

## 范围

- 研究 Hermes 插件 API（已有 pre_llm_call Python 插件）是否支持 session 结束/空闲事件
- 备选：定时扫描 Hermes state.db 新消息（只读连接 + WAL，跨进程安全）
- 输出：结论 + 推荐实现（写回 spec 的触发源部分）

## 验收

1. 明确结论：插件事件存在 / 不存在 / 需折中
2. 备选方案（state.db 扫描）的 schema 与安全要点记录
3. 结论写入本 ticket 的 ## Answer，并回写 spec.md

## 相关文件

C:\Users\Asus\AppData\Local\hermes\（插件与 state.db 只读探查）、
docs/plans/v0.5-dialogue-loop.md、.scratch/dialogue-loop/spec.md

## Answer（2026-08-09 研究完成）

### 结论：插件事件存在 ✅（首选方案可行）

Hermes Python 插件 API 支持会话结束事件，且桌面版（gateway）与 CLI 共用触发路径：

| 事件 | 触发时机 | payload（关键字段） | 证据位置 |
|---|---|---|---|
| `on_session_end` | **每轮对话结束**（run_conversation 尾部，CLI+桌面版通用） | session_id / task_id / turn_id / completed / failed / interrupted / turn_exit_reason / model / platform | hermes-agent/agent/turn_finalizer.py:739（"Fired at the very end of every run_conversation call"）；cli.py:1318/17826（中断兜底） |
| `on_session_finalize` | 真实会话边界（CLI 退出 / 会话过期清理） | session_id 等 | hermes_cli/hooks.py:170 枚举 |
| `on_session_reset` | /new 新会话 | session_id / old_session_id / new_session_id | gateway/slash_commands.py:306 |
| `pre_gateway_dispatch` | 网关每条用户消息（桌面版入站） | event（含文本）/ gateway / session_store | gateway/run.py:14286 |
| `pre_llm_call`（现有插件已用） | 每轮 LLM 调用前 | **user_message / conversation_history** / session_id | hermes_cli/hooks.py payload 样例 |

官方插件佐证：`plugins/observability/nemo_relay` 与 `plugins/disk-cleanup`、`plugins/google_meet` 均注册 `on_session_end`；memory provider 接口 `on_session_end(messages)` 会带完整对话（memory_manager.py:865）。

**关键约束**：`on_session_end` 插件 hook 的 payload **不含对话文本**——需插件自行缓冲
每轮 `pre_llm_call` 的 user_message（Supermemory 官方插件同款模式：
initialize 建 `_session_turns` → pre_llm_call/sync_turn 入缓冲 → on_session_end flush）。

### 备选方案：state.db 只读扫描（已验证 schema，可作兜底）

Hermes home = `C:\Users\Asus\AppData\Local\hermes\state.db`（实测 570 sessions / 11780 messages）：

- `sessions(id, source, user_id, started_at, ended_at, end_reason, message_count,
  title, last_activity_at, ...)`——source ∈ {desktop, cron, ...}；ended_at 空=进行中
- `messages(id, session_id, role, content, tool_call_id, timestamp, active,
  display_kind, ...)`——role ∈ {user, assistant, tool}；timestamp 为 epoch 秒(float)
- 已带 messages_fts_trigram 全文索引（可按内容检索）
- **安全要点**：只读连接 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`；
  Hermes 运行中带 WAL（-wal 文件）也能安全读；增量游标用 `sessions.last_activity_at`
  + `messages.timestamp`（浮点秒）；只取 `role='user' AND active=1`；
  跨会话重复写入由 Remembrance content_hash 去重兜底。

### 推荐实现（回写 spec）

**首选：插件 on_session_end 缓冲 flush（实时）**
1. 现有 remembrance-hook 插件加会话缓冲：`pre_llm_call` 回调已拿 user_message，
   按 session_id 累积到内存 dict（带锁，上限防膨胀）
2. 新增 `on_session_end` 回调：把缓冲的 user_message 列表批量调
   `add_dialogue` MCP（或直接子进程调 ingest_dialogue），随后清空缓冲
3. 降级：子进程/memory 服务不可用 → 丢弃本次缓冲不阻塞 Hermes（零侵入）

**兜底：cron 每日只读扫描 state.db（隔日感知）**
- 新 worker `remembrance/workers/hermes_scan_worker.py`：
  只读连接 → 按 last_activity_at 增量取 user 消息 → ingest_dialogue
- 覆盖插件未加载 / 崩溃 / 桌面版未运行等场景；content_hash 去重防重复

**不做**：不读 assistant/tool 消息（只提炼用户陈述与指令）；
不在插件里直接调 LLM（复用现有 serve 子进程 + ingest 链）。
