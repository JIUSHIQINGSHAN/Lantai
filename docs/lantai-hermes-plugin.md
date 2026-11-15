# Hermes 插件：记忆注入 + 对话自动写入

插件源码维护在仓库 `hermes-plugin/lantai-hook/`（版本化、可测试），
部署到 Hermes home 的 `plugins/lantai-hook/`。

## 功能（v1.1.0）

| 钩子 | 作用 |
|---|---|
| `pre_llm_call` | 每轮对话前检索 兰台记忆 记忆注入 user message；同时把 user_message 累积到会话缓冲 |
| `on_session_end` | 每轮对话结束触发（桌面版/CLI 通用），把缓冲 flush 给对话写通道 → `ingest_dialogue` |

对话写通道语义（`lantai/ingestion/dialogue.py`）：
- 「记住：X」/ 自我声明 / 偏好表达 → fastpath 直通
- 闲聊（过短/社交结束语）→ 直接 rejected（沙汰，ADR-0026，不进待审队列）
- LLM 提取低置信度 / 上游失败 → 兜底候选入队（不丢数据）

来源链与注入回执（P0 票02，2026-09）：
- 会话缓冲条目携带 `turn`（会话内 1-based 序号）；flush 时每条
  `{"type":"dialogue","text","session_id","turn"}` 透传 → 候选 `session_id`
  列 + `provenance.origin_turn`（缺会话留空、缺轮次 None，不补 0 不猜）
- 注入回执**延后到 `on_session_end`**（v0.67 票 01，ADR-0084 §2.1）：pre_llm_call
  把响应 `event_id` + `evidence[].id` 挂到会话缓冲条目上，flush 时该轮**未被否决**
  才发 `{"type":"backfill"}` → `RetrievalEvent.used_ids` 落库（弱标注「已注入」）。
  轮中途崩溃 → 回执保持 pending → 服务端 `mark_missed_receipts` 如实置 missed
  （宁 miss 不脏写）；被宿主否决的轮次改发 `{"type":"retrieval_veto"}` 作废检索
  事件，不刷假注入计数；无会话缓冲的轮次（无 session）保持立即回执旧行为。
  回执失败静默，不影响注入
- serve 协议动作：`{"type":"backfill","event_id","used_ids"}` 与
  `{"type":"retrieval_veto","event_id"}`（否决作废，ADR-0084）

安全边界（插件绝不阻塞/搞崩 Hermes）：
- 子进程启动失败/失活 → 静默降级
- 注入 5s 超时、对话写入 35s 超时（含 LLM 提取）。插件墙 = 服务端墙 + 宽限
  （`SHELL_HOOK_TIMEOUT` + 3s 且不低于 5s；`SHELL_HOOK_DIALOGUE_TIMEOUT` + 5s），
  从环境变量或仓库根 `.env` 读取，与 serve 子进程同口径——服务端墙先到、回降级帧、
  后台写入照常落库；插件 watchdog 只兜「子进程真卡死」，不再与服务端等宽竞态
- 会话缓冲有界：200 条 / 20 万字符，超出丢最旧
- 非主会话只读：Hermes 每轮带 `platform`，`cron`（Hermes 定时任务）、`subagent`（委派出去的
  子智能体）两类只注入、不写——不进会话缓冲、不写底本「上次在做」、不进对话写通道、
  不回执（事件留 pending，300s 后由服务端如实置 missed 并抹原话）。与 Hermes 自家
  MemoryProvider 接口「non-primary 上下文跳过写入」同口径。可用环境变量或仓库根 `.env` 的
  `LANTAI_HOOK_READONLY_PLATFORMS` 覆盖（逗号分隔；写成空值即关闭隔离）。旧版 Hermes 不传
  `platform` 时按主会话处理，与之前一致

## 部署

```bash
# 默认 Hermes home 自动探测：HERMES_HOME 环境变量 → Windows %LOCALAPPDATA%\hermes → 其他 ~/.hermes
# （scripts/install_hermes_plugin.py::get_default_hermes_home）
python scripts/install_hermes_plugin.py
# 自定义路径：
python scripts/install_hermes_plugin.py --hermes-home <path>
```

脚本行为：备份现有插件到扫描目录外的 `plugins-backup/lantai-hook-YYYYMMDD`（不删除，
备份内 `plugin.yaml` 改名为 `plugin.yaml.disabled`，避免被插件加载器当作同名候选扫描）→
复制新文件 → 自检无同名冲突 → 提示重启 Hermes。

## 验证

1. 重启 Hermes 桌面版，日志出现 `on_session_end 对话写入已注册`
2. 与 Hermes 聊一轮（说几句有价值的话；纯闲聊会被沙汰为 rejected、不进待审队列，
   这是 ADR-0026 的预期行为而非部署失败），结束后：
   - `GET /candidates/pending` 应出现候选（或已直通进记忆库）
   - `GET /usage` 每日新增数增长
   - 候选应带 `session_id` 与 `provenance.origin_turn`（来源链贯通）
   - 检索命中的轮次：`retrieval_event.used_ids` 非空（注入回执落库）
3. 回滚：删除 `plugins/lantai-hook`，把 `plugins-backup/lantai-hook-YYYYMMDD`
   移回 `plugins/lantai-hook`，并把备份内 `plugin.yaml.disabled` 改回 `plugin.yaml`，然后重启

## 开发

- 插件不 import lantai/Hermes 任何库（`register(ctx)` 注入），可独立测试：
  `tests/test_hermes_plugin.py`（缓冲/flush/回调注册，mock 子进程交互）
- serve 子进程协议扩展：`{"type": "dialogue", "text": ...}` →
  `{"ok": true, ...ingest_dialogue 结果}`；缺省 type 保持注入语义（向后兼容）
