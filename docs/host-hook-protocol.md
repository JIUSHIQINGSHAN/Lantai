# 宿主接入协议（Host Hook Protocol）

**状态**：当前契约的权威描述（活的规格）
**实现**：`lantai/integrations/host_protocol.py`（归一化层）+ `scripts/shell_hook.py`（宿主实现）
**决策沿革**：[ADR-0006](adr/0006-shell-hook-contract.md)（Shell Hook 注入契约，时点决策快照）
**回执链**：[ADR-0049](adr/0049-receipt-chain.md)

> 本文件与 ADR-0006 分工：ADR 记录**当时为何这样定**（时点决策），本文件记录**现在长什么样**（当前契约）。实现变更须同步本文件；ADR 不追溯改写。

---

## 1. 形态与调用方式

宿主以**子进程**方式调用兰台 hook，走 **NDJSON**（一行一个 JSON 请求 → 一行一个 JSON 响应）：

```
stdin:  {"query": "部署怎么做", "session_id": "sess-1"}
stdout: {"context": "...", "evidence": [...], "event_id": "rev_...", "request_id": "req_..."}
```

两种运行模式：

| 模式 | 命令 | 用途 |
|---|---|---|
| 单次 | `python scripts/shell_hook.py` | 读一次 stdin → 写一次 stdout |
| 常驻 | `python scripts/shell_hook.py --serve` | NDJSON 循环，每行一请求一响应；热进程消除冷启动 |

**I/O 编码**：进程启动即强制 stdin/stdout 为 UTF-8。Windows 默认 GBK 解码会让中文变乱码（「你好」→「浣犲ソ」）致检索零命中——此步不可省。

**宿主标识 `LANTAI_HOST`（宿主侧必须设置）**：hook 按环境变量 `LANTAI_HOST` 决定响应帧形状（见 §4.1）。**不设置 = 直通兰台自有形状**（`{"context": ...}`）。故：

| 值 | 行为 |
|---|---|
| 未设置 / 空 / `hermes` | 直通兰台自有形状 |
| `claude-code` / `codex` | 包成 `hookSpecificOutput.additionalContext` |

一个由本协议实现的新宿主**必须**在调用 hook 时设置 `LANTAI_HOST`，否则拿不到 `additionalContext`，注入静默失效。安装脚本生成的命令已包含该设置。

---

## 2. 动作（actions）

请求按 `type` 字段分派；**无 `type` 或 `type` 未匹配时回落 `query`**。

| 动作 | 触发 | 用途 |
|---|---|---|
| `query`（缺省） | 无 `type`，或 `type` 未匹配，且带查询字段 | 检索并注入上下文（读路径） |
| `dialogue` | `"type": "dialogue"` | 写入一轮对话（写路径） |
| `backfill` | `"type": "backfill"` | 注入回执：哪些召回记忆真被用上 |
| `checkpoint` | `"type": "checkpoint"` | 读取上次会话五段快照（底本注入） |
| `checkpoint_write` | `"type": "checkpoint_write"` | 落五段快照（会话结束） |

### 2.1 `query`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` / `message` / `prompt` | str | 其一 | 查询串，按 `query` > `message` > `prompt` 回退 |
| `session_id` | str | 否 | 会话标识，经归一化；非法 → 不落（宁 miss） |

**响应**：

| 字段 | 说明 |
|---|---|
| `context` | 注入上下文（含樊篱包裹的记忆正文）；无命中/超短查询 → 无此键 |
| `evidence` | 被注入记忆的 `[{id, content, score}]` |
| `event_id` | 本次检索事件标识，**回执凭据** |
| `request_id` | 本次注入调用整体标识（回执对账用，ADR-0049） |

**命中为空、查询过短（≤ `SHELL_HOOK_MIN_CHARS`，默认 3）或异常 → 返回 `{}`。**

### 2.2 `dialogue`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | str | 是 | 对话文本；空/纯空白 → 整帧拒绝（`{}`） |
| `session_id` | str | 否 | 缺省 `""`；经归一化 |
| `turn` | int | 否 | **1-based**；0/负数/非整数/布尔 → 视为无（`None`） |

响应：`{"ok": true, ...}`；失败/超时 → `{}`。

### 2.3 `backfill`（回执）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `event_id` | str | 是 | 注入时返回的 `event_id` |
| `used_ids` | list[str] | 是 | 真被用上的记忆 id；**整体覆盖语义** |
| `request_id` | str | 否 | 与事件列核对，不一致只记日志不拒绝（归属以 `event_id` 为准） |

**`used_ids` 为空、非列表、或含非字符串元素 → 整帧拒绝（`{}`）**——空表回执会抹掉既有弱标注，故拒绝而非接受。

响应：`{"ok": true, "event_id": ..., "used_count": N, "receipt_status": "acked"}`；失败 → `{}`。

### 2.4 `checkpoint` / `checkpoint_write`

| 动作 | 字段 | 说明 |
|---|---|---|
| `checkpoint` | 无 | 读上次会话五段快照；无快照/异常 → `{}` |
| `checkpoint_write` | `session_id`（str）、`blocks`（dict） | 缺任一或类型错 → `{}` |

> **已知不一致（如实登记）**：`checkpoint_write` 的 `session_id` **不**经归一化函数（`query`/`dialogue` 经）。此为抽取前的既有差异，为免改变已落库的来源链值而保留。后续如需统一，须评估存量数据迁移。

---

## 3. 超时与降级

| 设置 | 默认 | 适用 |
|---|---|---|
| `SHELL_HOOK_TIMEOUT` | 2s | `query` / `backfill` / `checkpoint` / `checkpoint_write` |
| `SHELL_HOOK_DIALOGUE_TIMEOUT` | 30s | `dialogue`（含 LLM 提取） |

- **超时只返回空帧**：部分结果比无结果更危险（误导 LLM），故超时 → `{}`。
- **异常一律静默降级**：hook 绝不抛异常、不拖垮宿主。
- **畸形/非对象 JSON 帧**（`null`/`[1,2]`/`123`/`"str"`）→ `{}`。常驻模式下逐个降级，**进程不被打死**。
- **单宿主故障隔离**：任一宿主的坏帧/超时不影响其他宿主与服务端。

---

## 4. 宿主矩阵

| 宿主 | 通道 | 计入命令钩子矩阵 |
|---|---|---|
| **Hermes** | 既有插件（`hermes-plugin/lantai-hook/`） | ✅ |
| **Claude Code** | `type: command` 钩子（`SessionStart` / `UserPromptSubmit`） | ✅ |
| **Codex CLI** | hooks.json / `config.toml [hooks]`（须开 `features.hooks`） | ✅ |
| **Cursor** | 无命令钩子 → 静态规则（`AGENTS.md` + `.cursor/rules/*.mdc`） | ❌ **降级档** |

**Cursor 为何不计入**：调研（2026-09-25 一手来源）核实 Cursor 无命令型钩子入口，只能做**静态规则注入**（规则内容在模型上下文开头注入，无运行时检索、无回执）。把它混进命令钩子矩阵会让「≥3 宿主端到端冒烟」的口径名不副实。其降级档由 `scripts/install_host_hooks.py --host cursor` 生成，如实交付但不冒充命令钩子。

**Codex 阻塞语义不依赖**：官方 config-reference 未载其返回值/阻塞语义（未确认），本协议只覆盖**读路径注入**，不基于未确认项做实现。

### 4.1 宿主侧接入方式

`scripts/install_host_hooks.py --host <宿主>` **默认只打印**配置片段（不写宿主目录，宁 miss 不脏写）；`--write` 才落盘并打印落点（落点已存在则拒绝覆盖）。

| 宿主 | 落点 | 片段要点 |
|---|---|---|
| `claude-code` | `.claude/settings.json` | `hooks.UserPromptSubmit` → `type: command` |
| `codex` | `.codex/hooks.json` | `features.hooks: true` + `hooks.UserPromptSubmit` |
| `cursor` | `.cursor/rules/lantai.mdc` | 静态规则（`alwaysApply: true`），**降级档** |

**响应形状差异**：Hermes 直接消费兰台自有形状（`context`/`evidence`/`event_id` 顶层）；Claude Code 与 Codex 走 `hookSpecificOutput.additionalContext` 通道，兰台元数据（`event_id`/`request_id`/`evidence`）作为**顶层兄弟字段**带出——宿主侧 wrapper 捕获 `event_id` 供稍后回执。未知顶层字段在 CC `UserPromptSubmit` 上被静默忽略（仅记 debug 日志），不报错；**仅 `Stop` 事件严格校验**，本协议不用该事件。

**写路径结果不翻译**：`backfill`/`dialogue`/`checkpoint*` 的控制面应答（含 `receipt_status`）原样返回——包成 `additionalContext` 会抹掉宿主侧所需的字段。

---

## 5. 接一个新宿主

1. 读本文件，确认宿主有命令钩子入口（无则只能走降级档）；
2. 写一个**薄帧映射**（宿主的字段名 → 本协议字段名；无改名需求则直通）；
3. 用真实协议帧跑冒烟（注入 → 回执 → 故障隔离三步）；
4. 在 §4 表格登记，注明是否计入命令钩子矩阵。

归一化层不含业务逻辑、handler 不含协议逻辑——接新宿主只加映射 + 配置，不动协议核心。
