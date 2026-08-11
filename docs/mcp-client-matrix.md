# 兰台记忆 MCP 客户端矩阵（跨客户端接入指南）

> 调研依据：agentmemory 以「一个 memory server 服务 32+ 客户端」验证跨客户端单
> server 模式成立；上游 aiduMEM 的 MCP 仍是 TODO，兰台已具备先发。本文档给
> 各客户端的最小接入配置 + 验证清单。

## 统一前提

- 服务端：`scripts/mcp_server.py`（标准 MCP stdio JSON-RPC，协议 `2024-11-05`）
- 启动命令（与 cwd 无关，脚本内部已把仓库根加入 sys.path；Windows 已强制 UTF-8）：
  ```bash
  python C:\Users\Asus\Desktop\记忆\scripts\mcp_server.py
  ```
- 数据目录：`LANTAI_HOME`（或 `REMEMBRANCE_HOME`）环境变量；未设置时用仓库默认目录
- 双形态分工：Shell Hook 做读（上下文注入），MCP 做写/查（search/add/raw_add/
  rollback/conflicts/candidates 等）

## 各客户端接入

### Claude Code（已验证命令形态）
```bash
claude mcp add lantai -- python C:\Users\Asus\Desktop\记忆\scripts\mcp_server.py
claude mcp list          # 确认 lantai 已注册
```

### Cursor
`.cursor/mcp.json`（项目级）：
```json
{
  "mcpServers": {
    "lantai": {
      "command": "python",
      "args": ["C:\\Users\\Asus\\Desktop\\记忆\\scripts\\mcp_server.py"],
      "env": { "LANTAI_HOME": "C:\\Users\\Asus\\Desktop\\记忆" }
    }
  }
}
```

### Gemini CLI / Codex CLI / 其他标准 MCP 客户端
均为标准 MCP stdio 形状，仅配置入口不同（Gemini 用 `gemini config` 的 mcpServers，
Codex 用 `~/.codex/config.toml` 的 mcp_servers；具体 CLI 旗标见各客户端文档）：
```json
{
  "mcpServers": {
    "lantai": {
      "command": "python",
      "args": ["C:\\Users\\Asus\\Desktop\\记忆\\scripts\\mcp_server.py"]
    }
  }
}
```

### Hermes（既有接入）
经 `scripts/install_hermes_plugin.py` 部署 lantai-hook 插件；MCP 由插件内嵌配置指向
同一 server。

## 工具清单（20）

| 工具 | 用途 |
|---|---|
| search / add / feedback | 检索 / 写入 / 有用性反馈 |
| backfill | 弱标注回填（检索事件 → 实际使用的记忆 id） |
| add_dialogue | 对话写通道（提炼候选，闲聊入队） |
| candidates_pending / candidate_review | 待审队列查看 / 裁决 |
| raw_add | 原文直存（verbatim 零 LLM，sha256 幂等） |
| rollback | 按 Checkpoint 回滚记忆 |
| conflicts_list / conflict_resolve | 冲突账本查看 / 裁决 |
| scene_get / scenes_list | 场景层（ADR-0012）查询 |
| recall_report | 零召回率监控报告（最近 N 天聚合） |
| get_digest | 今日记忆盘点（摘要 + 五项统计） |
| mem_help / mem_sync / mem_create_skill | mem: 命令式维护（帮助 / 资产刷新 / 沉淀 Skill） |
| obsidian_sync | Obsidian 笔记同步（原文直存 + [[双链]] 实体/边沉淀） |
| offload_read | 读取卸载全文（长记忆经上下文卸载后，按 memory_id 取回完整原文） |

## 验证清单（接入后逐条过）

1. `initialize` 返回 `serverInfo.name == "lantai"`、`protocolVersion == "2024-11-05"`
2. `tools/list` 返回 ≥14 个工具（当前 20），每个都有 description + inputSchema
3. `ping` 有响应；`notifications/initialized` 无响应（不报错）
4. `tools/call search` 传中文 query 返回结果（验证 UTF-8 无乱码）
5. 用 `raw_add` 写一条原文 → `search` 能召回（验证写读闭环）
6. 无权限错误、超时按各客户端默认（stdio 进程常驻）

> 合规回归已固化在 `tests/test_mcp.py`（tools 元数据 / ping / initialized /
> tools/call 缺参错误码），改 server 前先跑该文件。