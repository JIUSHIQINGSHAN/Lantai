# ADR-0022: 底本闭环——Shell Hook 注入通道 + Hermes 插件会话首轮注入

**日期**: 2026-08-15
**状态**: Accepted
**决策者**: 大哥
**来源**: v0.15 路线计划书 B 项（ADR-0021 follow-up）

## 背景

ADR-0021 落地了底本（五段会话快照）的 service 层 + REST + MCP，明确留项：
「Shell Hook 自动注入留后续（Hook 注入体积预算已满，需先定预算再接入，宁 miss 不脏写）」。
本 ADR 收口注入端（读取）与写入端（插件会话结束落快照）闭环。

## 决策

| 项 | 决策 |
|----|------|
| 注入通道 | `shell_hook --serve` 消息协议新增 `{"type": "checkpoint"}`——返回 `inject_checkpoint_context()` 文本（`{"context": ...}`）；无快照/异常返回空（零侵入降级）。**独立通道**：会话启动注入，不占每轮召回预算（`SHELL_HOOK_MAX_TOTAL_CHARS`）——底本是「上次会话续接」语义，非检索召回 |
| 写入通道 | serve 协议新增 `{"type": "checkpoint_write", "session_id", "blocks"}`——转 `write_session_checkpoint`（与 REST/MCP 同库同语义）；复用 serve 常驻进程，插件免 HTTP/免鉴权 |
| 插件首轮注入 | Hermes 插件 `pre_llm_call`：某 session_id 首次出现（`_checkpoint_injected` 集，容量上限 50）→ 先调 checkpoint 通道，注入文本与检索上下文合并（底本在前）；与触发词过滤无关（会话首轮必注入） |
| 插件写入 | `on_session_end`：用会话缓冲构建五段块 → `checkpoint_write`。**块来源规则（宁 miss）**：`cp_active_intent`（在做）= 末条 user 消息；`cp_next_action`（下一步）= 末条以 接下来/下一步/然后/待办 开头才填；`cp_key_decisions`（决策）= 末条含 决定/就按/采用/改为 声明句式才填；`cp_open_notes`（待办）= 末条含 别忘了/记得/待办 才填；`cp_current_work`（工作区）= **无可靠信号，v1 恒空**（宁 miss 不脏写） |
| 纯函数 | `build_session_blocks(messages) -> dict` 插件内纯函数（规则可测不 mock）；`_handle_checkpoint`/`_handle_checkpoint_write` 薄接线 |
| 预算边界 | checkpoint 注入文本 ≤ 五段 × 600 字符上限（service 截断），注入侧不追加截断；检索预算互不影响 |
| 安全 | 子进程失活/超时/异常 → 静默降级（返回空），插件不拖慢 Hermes（同 dialogue 通道既有模式） |

## 理由

- 复用 serve 常驻进程 + NDJSON 协议（dialogue 先例），零新进程、零 HTTP 面；
- 独立通道语义清晰：会话启动注入 vs 每轮召回，预算不互相挤占；
- 块来源规则保守：拿不准留空（宁 miss），五段块可信度优先；
- 首轮注入有界（`_checkpoint_injected` 容量 50，防会话集膨胀）。

## 影响

- `scripts/shell_hook.py`：`_handle_checkpoint` / `_handle_checkpoint_write` + `_handle_one` 两分支。
- `hermes-plugin/lantai-hook/__init__.py`：`_call_checkpoint` / `_call_checkpoint_write` /
  `build_session_blocks`（纯函数）/ 首轮注入跟踪 / `on_session_end` 落块。
- 测试：`test_checkpoint_service.py` 增 serve 协议分支（真实库）；`test_hermes_plugin.py`
  增首轮注入 + 落块（mock 子进程，纯函数不 mock）。
- 已知边界：`cp_current_work` v1 恒空；单 session 首轮后不再注入（续接语义一次足够）。

## 相关

- [ADR-0021](0021-session-checkpoint.md) — 底本 service 层
- [ADR-0006](0006-shell-hook-contract.md) — Hook 契约（serve NDJSON 协议）
- v0.15 路线计划书 B 项
