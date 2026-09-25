# 02 · 宿主钩子适配实证（Claude Code / Cursor / Codex CLI）

调研日期：2026-09-25。所有断言均挂当日检索的一手来源；未核实到的写「未确认」。

## 一、三宿主事实表

| 维度 | Claude Code | Cursor | OpenAI Codex CLI |
|---|---|---|---|
| 命令型钩子 | 有，事件→matcher→handlers 三层 JSON | 本次核查的 Rules 文档未提供命令型钩子（未确认是否有其他机制） | 有，`[hooks]` 表或 hooks.json，需 `features.hooks` 开关 |
| 注入时机（事件） | SessionStart/SessionEnd、UserPromptSubmit、PreToolUse/PostToolUse、Stop、Notification、PreCompact/PostCompact、SubagentStart/Stop 等 | 无事件钩子；规则注入：「rule contents are included at the start of the model context」 | PreToolUse、PermissionRequest、PostToolUse、PreCompact/PostCompact、SessionStart/SessionEnd、SubagentStart/Stop、UserPromptSubmit、Stop、Interrupt |
| 配置载体 | `~/.claude/settings.json`、`.claude/settings.json`、`.claude/settings.local.json`、插件 `hooks/hooks.json`；多层合并不替换 | `.cursor/rules/*.mdc`（frontmatter：alwaysApply/globs/description）+ 根/子目录 AGENTS.md | `~/.codex/config.toml`（`notify`、`[hooks]`）、hooks.json；AGENTS.md（全局 `~/.codex` + 项目根向下至 cwd） |
| 输入/控制 | stdin 收 JSON（session_id、tool_name、tool_input 等）；exit 0 成功；exit 2 阻塞（PreToolUse 拦工具、UserPromptSubmit 拒 prompt）；stdout 合法 JSON 可给 permissionDecision | 无交互控制；四类规则：alwaysApply=true 恒注入；globs 自动挂接；description 供 Agent 自取；@提及手动 | `notify` 外部命令收 JSON 事件；handler 仅支持 command/MCP tool，prompt/agent handler「parsed but skipped」 |
| 匹配语法 | matcher：`*`/省略=全部；`Edit\|Write` 精确；其余按无锚定 JS 正则（`mcp__memory__.*` 需 `.*`）；工具事件另可加 `if`（权限规则语法） | frontmatter 三字段决定四种应用方式 | 本页未给出 matcher/阻塞语义细节（未确认） |
| 已知限制 | 命令默认超时 600s；SessionEnd 共享 1.5s 预算；无控制终端；`if` 过滤 best-effort，硬安全须用权限系统 | 规则 <500 行；User Rules 不作用于 Inline Edit；优先级 Team→Project→User | `notify` 在项目级 config.toml 中被忽略；AGENTS.md 合计上限 `project_doc_max_bytes` 默认 32KiB |

来源（均检索 2026-09-25）：
- Claude Code hooks：https://code.claude.com/docs/en/hooks
- Cursor Rules：https://cursor.com/docs/context/rules
- Codex config reference：https://learn.chatgpt.com/docs/config-file/config-reference
- Codex AGENTS.md：https://learn.chatgpt.com/docs/agent-configuration/agents-md
- Codex 仓库 docs/config.md（`allow_managed_hooks_only = true` 仅 requirements.toml 有效）：https://github.com/openai/codex/blob/main/docs/config.md

## 二、AGENTS.md 公共约定（三宿主中两个直接消费）

- AGENTS.md 是开放格式约定（「a README for agents」，纯 Markdown、无必填结构），由 OpenAI Codex、Cursor、Jules 等共同发起，现由 Linux 基金会旗下 Agentic AI Foundation 托管；「closest AGENTS.md to the edited file wins」。来源：https://agents.md/（检索 2026-09-25）
- Cursor：子目录 AGENTS.md 自动作用于该目录及子目录，与父级合并、更近者优先。来源：cursor.com/docs/context/rules（检索 2026-09-25）
- Codex：每次运行从根向下拼接 AGENTS.override.md → AGENTS.md → fallback 名（可配 `project_doc_fallback_filenames`），深者优先；无缓存。来源：learn.chatgpt.com/docs/agent-configuration/agents-md（检索 2026-09-25）
- Claude Code：本调研未核对其 AGENTS.md 支持细节（未确认；其上下文文件机制另行核实）。

## 三、兰台 shell hook 泛化到三宿主的最小接入路径（只列事实依据，不设计）

1. **Claude Code**：兰台 hook 即为现成的 `type: command` handler，可直接放入 `.claude/settings.json` 的 `hooks.SessionStart` / `hooks.UserPromptSubmit` / `hooks.PreToolUse`，stdin JSON 与 exit 2 阻塞语义已在官方参考中定义。事实依据：code.claude.com/docs/en/hooks。
2. **Codex CLI**：事件名与 Claude Code 高度同名（PreToolUse/PostToolUse/UserPromptSubmit/SessionStart/SessionEnd/Stop 等），载体改为 hooks.json 或 `config.toml [hooks]`，并须开启 `features.hooks`；`PreToolUse` 能否阻塞工具调用在 config-reference 未说明（未确认，接入前需实测）。事实依据：learn.chatgpt.com/docs/config-file/config-reference。
3. **Cursor**：无命令钩子入口可依赖；可依据的事实路径只有两条——`.cursor/rules/*.mdc`（把兰台使用规范作为规则注入，支持 alwaysApply 或 globs 自动挂接）与根/子目录 `AGENTS.md`。事实依据：cursor.com/docs/context/rules。
4. **三宿主共同兜底**：AGENTS.md 在 Cursor 与 Codex 均被原生读取且遵循「最近文件优先」，兰台可在仓库根放置一份 AGENTS.md 作为最低公分母。事实依据：agents.md/ + 各宿主文档。

## 四、空白与未确认项

- Codex hooks 的返回值/阻塞语义、matcher 语法：config-reference 未载（未确认）。
- Cursor 是否存在任何命令执行型 hook：所核文档页未提及（未确认）。
- Claude Code 对 AGENTS.md 的原生读取规则：本轮未核实（未确认）。
