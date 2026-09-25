# 技术规格：宿主矩阵——Shell Hook 契约泛化为宿主适配层

- 日期：2026-09-26
- 票据：`.scratch/roadmap-v2-execution/issues/06-p1-host-matrix.md`（P1-2）
- Roadmap：`docs/research/memory-landscape-2026-09/roadmap-v2.md:24`（验收口径：≥3 宿主端到端冒烟）
- 依据：iter-13 D24（churn：以宿主矩阵 + 注入回执替代 MCP 工具扩容）
- 前置：票 04（回执契约随宿主协议一次定型）
- 设计对话记录：2026-09-26 四问四定（全部选 (a)），维护者确认「可行」

---

## 1. 问题与目标

**问题**：兰台只有**一个**宿主钩子（Hermes 插件 + `scripts/shell_hook.py`）。宿主单点使「来源链 + 回执」治理承诺只对单宿主可证明——roadmap 的 integration 分项 3.5 分（对照 agentmemory 4.5），差距在**宿主矩阵**而非工具面（MCP 59 工具已足量）。且 shell hook 的 NDJSON 契约（动作/字段/超时/降级/回执）目前**散落在代码注释**中，未成一份宿主接入协议。

**目标**：把 shell hook 契约泛化为**宿主适配层**，使同一注入协议可被多个真实宿主驱动；交付 **≥3 宿主端到端冒烟**。

**非目标**：不扩 MCP 工具面；不做宿主 GUI/云 API 集成；不移植 OpenViking/MemU 代码；不承诺宿主内已注入副本的撤回清除（ADR-0047 边界）。

---

## 2. 关键前提（调研实证，2026-09-25 一手来源）

宿主可行性并非三者同构，`followups/2026-10/02-host-hook-integrations.md` 实证：

| 维度 | Claude Code | Cursor | OpenAI Codex CLI |
|---|---|---|---|
| 命令型钩子 | 有（事件→matcher→handlers，`type: command`） | **无**（所核文档未提供命令钩子） | 有（`[hooks]`/hooks.json，需 `features.hooks`） |
| 注入时机 | SessionStart/UserPromptSubmit/PreToolUse 等 | 规则注入（`.cursor/rules/*.mdc` + AGENTS.md） | PreToolUse/UserPromptSubmit/SessionStart 等 |
| 输入/控制 | stdin JSON，exit 2 可阻塞 | 无交互控制 | `notify` 收 JSON；**阻塞语义未载（未确认）** |

**由此得出两项设计约束**：

1. **Cursor 出局命令钩子矩阵**——它没有可执行脚本的钩子入口，接入面只有静态规则（`.mdc`/AGENTS.md）。把它混进「命令钩子矩阵」会让「≥3 宿主端到端冒烟」的验收口径名不副实（命令钩子宿主冒烟：真实子进程 + stdin/stdout 帧 + 非空 context + 可回执；规则注入：静态文件存在性检查——两者不同构）。
2. **不依赖 Codex 阻塞语义**——官方 config-reference 未载其返回值/阻塞语义；本票只做**注入（读路径）**，绕开该未确认项，不硬猜。

**决策（2026-09-26，问题 1 选 (a)）**：命令钩子矩阵 = **Hermes + Claude Code + Codex CLI**；Cursor 作**降级档**（规则注入）单独如实记录，**不计入矩阵**。

---

## 3. 架构与模块边界

```
宿主（Hermes 插件 / Claude Code / Codex）
      │  stdin JSON 帧
      ▼
scripts/shell_hook.py          ← 宿主实现之一：读 stdin → 归一化 → 分发 → 渲染 → 写 stdout
      │  import
      ▼
lantai/integrations/host_protocol.py   ← 协议归一化层（纯函数，无 IO、无子进程）
      ├─ parse_host_request(raw) -> HostRequest | None
      └─ render_host_response(result) -> str

lantai/integrations/host_adapters.py   ← 各宿主帧翻译（薄，只做字段改名；无则直通）
```

**职责边界**（每单元一句话说清做什么 / 怎么用 / 依赖谁）：

| 单元 | 做什么 | 依赖 |
|---|---|---|
| `host_protocol` | 把任意宿主输入帧归一化为 `HostRequest`；把标准结果渲染回帧。纯函数、可直测 | 仅 `lantai.core.text`（`normalize_session_id`） |
| `host_adapters` | 按宿主名做字段改名（如 Claude Code `prompt`→标准 `query`）；无改名需求的宿主直通 | `host_protocol` |
| `shell_hook.py` | 进程边界 + 分发到既有 handler（dialogue/backfill/checkpoint/checkpoint_write/query）+ 2s 超时 | 上面两者 + 既有 handler |
| 既有 handler | **完全不动**（`build_context`/`_handle_*` 逐字保留） | — |

**设计原则**：归一层**不含业务逻辑**，handler **不含协议逻辑**。新增宿主 = 加一个字段映射 + 一份配置，不碰协议核心、不碰 handler。

**决策（问题 2 选 (a)）**：落点 `lantai/integrations/`（既有集成面已有 `pre_compress.py` 先例），而非 `scripts/` 侧或内联 `shell_hook.py`。

---

## 4. 归一化层契约

```python
@dataclass(frozen=True)
class HostRequest:
    action: str                 # "query" | "dialogue" | "backfill" | "checkpoint" | "checkpoint_write"
    query: str = ""
    session_id: str = ""
    turn: int | None = None     # 1-based；非法（0/负数/非 int/bool）→ None（宁 miss）
    text: str = ""              # dialogue 用
    event_id: str = ""
    used_ids: tuple[str, ...] = ()   # backfill 用；空/含非 str → 视为无效回执
    request_id: str | None = None
    blocks: dict = field(default_factory=dict)
```

- `parse_host_request(raw)`：空串/非 JSON → `None`；`type` 字段决定 action（无 `type` 且有 `query`/`message`/`prompt` 之一 → `query`）；字段校验规则**逐条平移现状**（`session_id` 经 `normalize_session_id`、turn 1-based 契约、`used_ids` 非空且全 `str`、`blocks` 须 `dict`）——把 `_handle_one` 里的校验原样搬入，**判定顺序与结果不变**。
- `render_host_response(result)`：`json.dumps(..., ensure_ascii=False)`，与现状逐字节相同。
- **超时不在本层**（本层无 IO）；超时仍是 `shell_hook` 的 `_run_with_timeout`，语义逐宿主保留。
- 契约的**权威文档**：`docs/host-hook-protocol.md`（见 §7）。

**行为等价证法**：`tests/test_shell_hook.py` / `tests/test_hermes_plugin.py` **断言零改动通过**（票面 TDD 口径原文）。

---

## 5. 宿主适配与配置

| 宿主 | 通道 | 帧映射 | 交付物 |
|---|---|---|---|
| **Hermes** | 插件（已有） | 无（现状即标准） | 不动；仅回归验证 |
| **Claude Code** | `type: command` 钩子 | `prompt`→`query`、`session_id` 直通 | `.claude/settings.json` 片段 + 适配器映射 |
| **Codex CLI** | `hooks.json` / `config.toml [hooks]`（须开 `features.hooks`） | 事件名与 CC 同名，同映射 | hooks.json 片段 + 适配器映射 |
| **Cursor**（降级档，**不计入矩阵**） | 无命令钩子 | — | `AGENTS.md` 片段 + `.cursor/rules/lantai.mdc` 静态规则（`alwaysApply`），内容取 `_build_tools_guide` 的用法指引 |

- **统一安装出口**：`scripts/install_host_hooks.py --host claude-code|codex|cursor`（默认只**打印**配置片段，**不写宿主目录**——宁 miss 不脏写；`--write` 才落盘且打印落点）。
- Codex 不依赖阻塞语义（§2 约束 2）；Cursor 降级档在 Comments 与 ADR 里**如实声明为何不计入命令钩子矩阵**。

---

## 6. E2E 冒烟与验证口径

**三条命令钩子宿主 E2E**（Hermes / Claude Code / Codex），每条：

1. **注入**：真实子进程 `python scripts/shell_hook.py`、真实 stdin/stdout 协议帧（不 mock 进程边界）、真实 SQLite+FTS 落 `RetrievalEvent`；写宿主形输入帧 → 读 stdout → 断言 `context` 非空且含 `event_id`、`session_id` 透传；
2. **回执**：以该 `event_id` 写 backfill 帧 → 断言返回 `receipt_status == "acked"`（接票 04 回执链，实测置位）；
3. **隔离**：单宿主帧超时/畸形 → 返回 `{}` 且**不影响**另两宿主与后续请求。

**测试落点**：新增 `tests/test_host_matrix.py`（参数化三宿主帧 fixture）；`test_shell_hook.py` / `test_hermes_plugin.py` 零改动通过。

**不 mock 要求**（票面）：真实子进程 + 真实协议帧；服务端真实 SQLite+FTS；外部无 LLM 调用面则零替身（embedding 走本地 ngram）。宿主侧脚本自身逻辑不得 mock。

**命名治理**（问题 3 选 (a)）：不起新名，统一用描述性短语「宿主适配层 / 宿主矩阵」；扩写 `CONTEXT.md` 既有「Shell Hook」条 + ADR-0006 加一行指向新协议文档。

---

## 7. 协议文档（问题 4 选 (a)）

新增 `docs/host-hook-protocol.md`——**当前契约的权威描述**，与 `docs/adr/0006-shell-hook-contract.md`（时点决策快照）明确分工：

- 动作：`query`（默认）/ `dialogue` / `backfill` / `checkpoint` / `checkpoint_write`；
- 字段：`query`/`message`/`prompt`、`session_id`、`turn`（1-based）、`text`、`event_id`、`used_ids`、`request_id`、`blocks`；
- 超时：`SHELL_HOOK_TIMEOUT`（默认 2s）、`SHELL_HOOK_DIALOGUE_TIMEOUT`；
- 降级：异常/超时返回 `{}`，静默不抛（插件不能拖垮宿主）；
- 回执语义：`backfill` → `receipt_status="acked"`（含票 04 回执链字段）。

ADR-0006 只加一行指向本文件的引用，不搬内容（保持 ADR 的时点决策语义）。

---

## 8. 边界与不做

- 不做宿主 GUI/平台云 API 集成；不移植 OpenViking/MemU 代码（只借鉴接入模式）；
- 不承诺宿主内已注入副本的撤回清除（ADR-0047 边界，缓解 = 回执链 + 下次注入刷新）；
- 不扩 MCP 工具面（59 个已足量，roadmap churn 明示）；
- 不做 Codex 阻塞语义（官方未载，未确认项不硬猜）；
- Cursor 只做静态规则注入，不假装它跑命令钩子。

---

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 抽层引入行为漂移 | 既有两测试文件**断言零改动通过**（等价性最简证法） |
| Codex 钩子行为实测与文档不符 | 本票不依赖阻塞语义；冒烟只覆盖注入 + 回执（文档已载的部分） |
| 宿主配置写坏用户目录 | `install_host_hooks.py` 默认只打印；`--write` 才落盘并打印落点 |
| 「≥3 宿主」口径被稀释 | Cursor 明示不计入矩阵并在 Comments/ADR 如实说明 |
