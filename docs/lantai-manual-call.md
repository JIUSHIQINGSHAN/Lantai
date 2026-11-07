# 兰台记忆 记忆主动调用手册

> 场景：不依赖 pre_llm_call 自动注入，**手动**触发记忆检索/写入/反馈。
> 工具名（Hermes 内）：`mcp__remembrance__search` / `mcp__remembrance__add` / `mcp__remembrance__feedback`

---

## 方式一：在 Hermes 对话里说人话（最常用 ⭐）

直接跟 Hermes 说，它会自动调用 `search` 工具：

```
帮我查一下记忆里关于XX的内容
记得之前讨论过的XX方案吗
搜索一下大哥的XX偏好
```

**注意 gate 规则**（决定 Hermes 是否真的去搜）：
- 查询 **≤15 字** 且不带「记得/上次/回忆/帮我查」等触发词 → 会返回 no_signal（设计如此，防误触发）
- **带回忆词 或 >15 字** → 正常检索
- ✅ 推荐说法：「**帮我查一下** + 具体内容」（"帮我查"命中 gate 规则）
- ⚠️ 短句「大哥的电脑配置」这种 → 大概率 no_signal

想存记忆：
```
帮我存到记忆：今天确定了XX方案
记住：大哥不喜欢XX
```

## 方式二：命令行直接调（绕过 Hermes）

一条命令直达 MCP server（`launcher_mcp.py` 会自己处理环境；入口为现行
`lantai.cli.mcp`，旧 `scripts/mcp_server.py` 已删除）：

```bash
cd <仓库根>   # 如 D:\Projects\记忆
# 检索（注意 query 要带回忆词或足够具体，见 gate 规则）
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"manual","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"query":"帮我查一下大哥的电脑配置是什么呀","top_k":3}}}
' | .venv/Scripts/python.exe launcher_mcp.py
```

写入记忆（add；`title` 必填非空，`content` 与 `media_url` 二选一且 content ≥10 字）：
```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"manual","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"title":"XX方案","content":"今天确定了XX方案的细节，先记录到记忆备用。","lane":"general"}}}
' | .venv/Scripts/python.exe launcher_mcp.py
```

反馈（标记某条记忆是否有用，供评估）：
```bash
# arguments: {"memory_id": "mem_xxx", "helped": true}
```

## 方式三：REST API（需先起服务）

```bash
cd <仓库根>   # 如 D:\Projects\记忆
.venv/Scripts/python.exe api_server.py   # 起服务（默认 8767 端口，settings.PORT）
```

```bash
curl -X POST http://127.0.0.1:8767/search \
  -H "Content-Type: application/json" \
  -d '{"query":"帮我查一下大哥的电脑配置是什么呀","top_k":3}'
```

## 三个工具速查

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `search` | 检索记忆 | `query`（必填，>15字或带回忆词）、`top_k`（默认5） |
| `add` | 写入记忆 | `title`（必填非空）、`content`（≥10 字）或 `media_url`（目识，二选一）、`lane`（默认 general） |
| `feedback` | 反馈有用性 | `memory_id`（必填）、`helped`（bool） |

## 常见问题

- **no_signal**：查询太短/无触发词 → 加「帮我查/记得/上次」或扩到 >15 字
- **搜不到中文**：编码问题已修复（2026-08-05），如再出现检查 stdin 编码
- **add 超时**：MCP 进程可能崩了 → `cd <仓库根> && .venv/Scripts/python.exe launcher_mcp.py` 重启
