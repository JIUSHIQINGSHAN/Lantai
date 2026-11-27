# 兰台记忆 MCP 客户端矩阵（跨客户端接入指南）

本文按仓库实际入口描述配置与验收。标准 MCP、宿主生命周期插件、MCP Apps 是三个
独立能力；握手成功不代表自动注入、对话采集或内嵌面板已在实际客户端生效。

## 统一前提

- 服务端入口：`lantai-mcp` console script（= `lantai/cli/mcp.py:main`，标准 MCP stdio
  JSON-RPC，协议 `2024-11-05`）。旧入口 `scripts/mcp_server.py` 已于 `bea001c1` 删除，
  不要再引用。
- 启动命令（需项目 venv：`uv sync` 后 lantai 为 editable 安装；未装时 cd 到仓库根
  `python -m lantai.cli.mcp` 亦可。Windows 已强制 UTF-8）：
  ```bash
  python -m lantai.cli.mcp    # 或 venv Scripts 里的 lantai-mcp
  ```
- 数据目录（四段回落，`lantai/core/settings.py` 为准）：`LANTAI_HOME` 显式设置 → 旧 `REMEMBRANCE_HOME` 兼容沿用 → 用户持久目录 `~/AppData/Local/remembrance-data`（仅该目录与 `remembrance.db` 均在才沿用）→ 仓库默认目录
- **工具档位（fail-closed）**：不设 `LANTAI_MCP_PROFILE`（或设空、设了不认识的值）时
  只暴露 `HERMES_CORE_TOOLS` 核心 6 工具：`search` / `add` / `feedback` /
  `offload_read` / `mem_recent` / `core_memory_get`；调用档外工具一律 `-32602`。
  唯一全量入口是显式 `LANTAI_MCP_PROFILE=full`（96 项注册表）。
- 双形态分工：Shell Hook 做读（上下文注入），MCP 做写/查（默认档 6 工具已覆盖
  search/add/feedback；`raw_add`/`rollback`/`conflicts`/`candidates` 等需 full 档）
- CLI 只有 `lantai-mcp`、`lantai-mcp-bridge`、`lantai-server` 三个安装入口。
  仓库没有 `lantai mcp` 或 `lantai hermes` 子命令。下方脚本从仓库根运行；源码入口
  绑定当前检出，移动仓库后须重新生成。配置、初始化数据目录、启用宿主是独立步骤。
- 各宿主显式选择同一个或各自独立的 `LANTAI_HOME`，配置非空 `API_KEY`，并先完成
  [数据库初始化](../README.md)。生成配置不迁移数据、不写凭据、不自动启用插件。

## 各客户端接入

### Claude Code / Codex / DSH（仓库配置生成器）

推荐使用 [三宿主接入包](../integrations/mirasim-lantai/README.md) 的纯预览入口，
避免 PATH 指向其他 Python 环境：

```powershell
python integrations/mirasim-lantai/configure.py --host claude-code --home D:/LantaiData
python integrations/mirasim-lantai/configure.py --host codex --home D:/LantaiData
python integrations/mirasim-lantai/configure.py --host dsh --home D:/LantaiData
```

默认仅打印配置；`--output <新目录>` 才导出审阅文件，已有目录拒绝覆盖。可用
`--python <绝对路径>` 选解释器。Claude Code/Codex 显式加 `--hooks` 才包含
SessionStart / UserPromptSubmit 命令钩子；Windows 钩子拒绝 `&`、`%`、括号等
命令解释器特殊字符路径，普通含空格路径可用。生成的 MCP 入口固定六工具核心档。

DSH 输出 `cordis.patch.yml`，连接 [逐调用归因插件](../integrations/dsh-lantai/README.md)，
默认禁用。不要和另一条同名 `lantai` 连接同时启用。该插件不自动启用
[核心记忆与对话采集](../integrations/dsh-core-memory/README.md)。

Claude Code 的原生命令形态另可参考：
```bash
claude mcp add lantai -- <venv>\Scripts\python.exe -m lantai.cli.mcp
claude mcp list          # 确认 lantai 已注册
```

### Cursor
`.cursor/mcp.json`（项目级）：
```json
{
  "mcpServers": {
    "lantai": {
      "command": "<venv>\\Scripts\\python.exe",
      "args": ["-m", "lantai.cli.mcp"],
      "env": { "LANTAI_HOME": "<数据目录>" }
    }
  }
}
```

### Antigravity / Gemini CLI / 其他标准 MCP 客户端

使用客户端提供的 MCP stdio 配置入口，填写以下 command / args / env。
这是通用配置形状，文件位置和导入方式由客户端自身决定；Codex 的 TOML 配置使用
上面的生成器。仓库没有 Antigravity 专用 Hook 或自动对话采集适配，不将标准 MCP
握手回归当成 Antigravity GUI 或 MCP Apps 的实机验收。
```json
{
  "mcpServers": {
    "lantai": {
      "command": "<venv>\\Scripts\\python.exe",
      "args": ["-m", "lantai.cli.mcp"],
      "env": { "LANTAI_HOME": "<数据目录>", "LANTAI_MCP_PROFILE": "core" }
    }
  }
}
```

### Hermes（既有接入）

`scripts/install_hermes_plugin.py` 只部署 `lantai-hook` Python 插件，不创建 MCP 配置。
MCP 须在 Hermes 实际读取的 `config.yaml` 的 `mcp_servers` 中单独配置到同一 server，
参见 [安装交接](hermes-install-handoff.md) 与 [插件说明](lantai-hermes-plugin.md)。
CLI Shell Hook 与该插件择一启用，避免重复注入。可显式设 `LANTAI_PYTHON` 和
`LANTAI_SHELL_HOOK` 为存在的绝对文件路径；显式空值、相对路径或目录会拒绝注册，
不会悄悄回落到另一份源码。插件自行启动的子进程固定 `LANTAI_HOST=hermes`。

### OpenClaw（原生生命周期插件）

[OpenClaw 接入包](../integrations/openclaw-lantai/README.md) 单独配置 `python`、
`shellHook`、属主及智能体/渠道/会话白名单。它通过常驻 NDJSON 子进程处理
`before_prompt_build` 和 `agent_end`，与 MCP 配置分别管理。对话采集默认关闭；
仅 `userId=default` 可显式开启。缺少原生运行身份、非用户触发或白名单不符均不执行。
真实插件入口和子进程已由契约测试覆盖，是否在运行中的客户端加载仍需另验。

## MCP Apps 边界

核心记忆面板与悬镜资源通过支持 MCP Apps 的宿主沙箱展示，不是独立网页服务。
初始化须由宿主传入工具上下文；后续查询需要 `hostCapabilities.serverTools`，缺少
该能力时只展示宿主推送的结果，刷新与服务端筛选禁用。主题支持 light/dark 与宿主
样式变量，接受直接或 `hostContext` 包装的主题通知。资源正文按纯文本展示。
悬镜工具 `get_xuanjing_graph` 属于 full 档；核心档不会为显示面板而自动扩大权限。
普通 `/ui#/xuanjing` 控制台通过同源 REST API 加载，与 MCP 沙箱通道独立。

浏览器验收使用真实 Chromium、本地 HTTP 服务和真实工具分派器；错误及搜索嵌套结果
在宿主消息边界注入合成帧。它证明产品页面与桥接行为，不代表六款客户端都已实机显示
MCP Apps。测试准备见 [开发测试环境](project/development-environment.md)。

## 工具清单（full 档节选，全量 96 项以 `tools/list` 实测为准）

> 下表为 full 档（`LANTAI_MCP_PROFILE=full`）注册表的节选；默认档部署只有
> [统一前提](#统一前提)列出的核心 6 工具，表内其余工具调用即 `-32602`。

| 工具 | 用途 |
|---|---|
| search / add / feedback | 检索 / 写入（add 支持 media_url 图片感知，v0.10 目识，content 二选一）/ 有用性反馈；search 可选 `session_id` 参数（来源链落检索事件，P0 票02）——三者均在默认档 |
| backfill | 弱标注回填（检索事件 → 实际使用的记忆 id）；需 full 档，REST 端点为主力通道（见 used-ids 指南） |
| add_dialogue | 对话写通道（提炼候选；闲聊沙汰为 rejected，ADR-0026） |
| candidates_pending / candidate_review | 待审队列查看 / 裁决（approve 仅生成 pending 提案，最终写入需再批准提案）；需 full 档 |
| raw_add | 原文直存（verbatim 零 LLM，sha256 幂等）；需 full 档 |
| rollback | 按 Checkpoint 回滚记忆；需 full 档 |
| conflicts_list / conflict_resolve | 冲突账本查看 / 裁决；需 full 档 |
| scene_get / scenes_list | 场景层（ADR-0012）查询 |
| recall_report | 零召回率监控报告（最近 N 天聚合） |
| get_digest | 今日记忆盘点（摘要 + 统计） |
| mem_help / mem_sync / mem_create_skill | mem: 命令式维护（帮助 / 资产刷新 / 沉淀 Skill） |
| obsidian_sync | Obsidian 笔记同步（原文直存 + [[双链]] 实体/边沉淀） |
| offload_read | 读取卸载全文（长记忆经上下文卸载后，按 memory_id 取回完整原文）——默认档 |
| wiki_read | 记忆 Wiki 下钻（先看 index/overview，再按 slug 取页面正文） |
| mem_recent | 最近记忆（只读，按更新时间倒序）——默认档 |
| mem_stats | 记忆概览（总数/分布/待审候选/检查点/待审提案，只读聚合） |
| mem_health | 深度健康检查（SQLite + 向量存储，不触发外部 LLM） |
| autodream_report / autodream_trigger | 蒸馏预演（dry-run 不写库）/ 执行一轮蒸馏（落待审提案，人工裁决才应用） |
| proposals_list / proposal_decide | 待审提案查看 / 裁决（approve 先落 Checkpoint 可回滚，reject 记 reason） |
| tree_view / tree_add / tree_assign | 记忆分类树：视图（含挂载计数）/ 建节点 / 挂载记忆（v0.7） |
| crystals_list / crystals_detect / crystal_decide | 技能结晶：候选项列表 / 检测（可 dry-run）/ 裁决（approve 需 steps） |
| reflect_run | 执行一轮反思（健康扫描 → 提案 → 裁决，高置信 auto-apply） |
| mem_usage | 用量统计（最近 N 天每日新增记忆数，缺日补零） |
| core_memory_get | 核心记忆块只读（identity/task/policy）——默认档 |
| verbatim_search | 原文直存专用检索（FTS+向量，不进混合召回） |
| graph_view | 记忆关系星图（只读）：节点 + MemoryEdge 链接 + lane/relation 统计（v0.9） |
| recall_chain | 记忆广播链（烽燧，只读）：seed 记忆逐层触发关联记忆（v0.11） |

## 验证清单（接入后逐条过）

1. `initialize` 返回 `serverInfo.name == "lantai"`、`protocolVersion == "2024-11-05"`
2. `tools/list` 数量按档断言：默认档返回 **6** 个工具；`LANTAI_MCP_PROFILE=full`
   返回 **96** 个，每个都有 description + inputSchema
3. `ping` 有响应；`notifications/initialized` 无响应（不报错）
4. `tools/call search` 传中文 query 返回结果（验证 UTF-8 无乱码）——默认档可验
5. 用 `raw_add` 写一条原文 → `search` 能召回（验证写读闭环）——**需 full 档**
   （默认档调用 raw_add 即 `-32602`，这不是部署故障）
6. 无权限错误、超时按各客户端默认（stdio 进程常驻）
7. `search` 传 `session_id` → 检索事件落 session（`retrieval_event.session_id`
   非空可查；写线活性判据「带 session 的读才算真实会话读」依赖此值，P0 票02）

> 合规回归已固化在 `tests/test_mcp.py`（tools 元数据 / ping / initialized /
> tools/call 缺参错误码），改 server 前先跑该文件。
>
> **Wire 格式变更（P0 票03，2026-09-19）**：search 的 `results[].memory.content`
> 与 `evidence[].content` 默认被樊篱 `<memory_data>` 围栏包裹（数据非指令声明见
> 响应 `data_fence_notice` 字段）——按内容做严格字符串比较的客户端需感知；
> 兼容开关 `DATA_FENCE_ENABLED=false` 可退回裸文本。
