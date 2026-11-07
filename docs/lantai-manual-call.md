# 兰台记忆 记忆主动调用手册

> 场景：不依赖 pre_llm_call 自动注入，**手动**触发记忆检索/写入/反馈。
> 工具名（Hermes 内）：`mcp__remembrance__search` / `mcp__remembrance__add` / `mcp__remembrance__feedback`——前缀源自 Hermes 配置里 `mcp_servers:` 的 **server key**（交接文档示例用的 key 是 `remembrance`），与 serverInfo.name=`lantai` 是两层命名；新部署建议 server key 直接用 `lantai`，前缀随之变为 `mcp__lantai__*`

---

## 方式一：在 Hermes 对话里说人话（最常用 ⭐）

直接跟 Hermes 说，它会自动调用 `search` 工具：

```
帮我查一下记忆里关于XX的内容
记得之前讨论过的XX方案吗
搜索一下大哥的XX偏好
```

**注意 gate 规则**（决定 Hermes 是否真的去搜；ADR-0028 放宽后口径，`lantai/gate/prefilter.py` 为准）：
- 命中任一放行通道即检索：纠错（不对/你记错）、自我指代（我的/我是/大哥）、
  明确回忆（记得/忘记/回忆/查…记忆）、指代延续（上次/之前/继续）、
  技术词 ≥4 字的短查询、问句 ≥5 汉字、>15 字且含实词；
  12 字内的短追问沿用上一轮**命中**判定（15s 热缓存；上轮 no_signal 不沿用，照走完整判定）
- 都不命中 → no_signal（设计如此，防误触发）
- ✅ 推荐说法：自然完整问句（「龙井茶冲泡水温是多少」——问句 ≥5 汉字已放行，
  不再被 15 字硬卡）或带「记得/上次」的回忆句
- ⚠️ 孤立短查询（如「物业费在哪交」——单字「哪」不在疑问模式、无技术词）→ no_signal
- 绕过闸门：`search` 传 `force: true` 显式直接检索（拾遗 ADR-0028）

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
| `search` | 检索记忆 | `query`（必填；放行口径见 gate 规则）、`top_k`（默认5）、`force`（true 显式绕过闸门） |
| `add` | 写入记忆 | `title`（必填非空）、`content`（≥10 字）或 `media_url`（目识，二选一）、`lane`（默认 general） |
| `feedback` | 反馈有用性 | `memory_id`（必填）、`helped`（bool） |

## 常见问题

- **no_signal**：查询未命中任何放行通道 → 加「记得/上次」等回忆词、补成 ≥5 汉字的
  问句或含技术词；命令行/REST 直调可传 `force: true`（「帮我查」单独出现**不在**
  触发正则内，单靠它不放行）
- **搜不到中文**：编码问题已修复（2026-08-05），如再出现检查 stdin 编码
- **add 超时**：MCP 进程可能崩了 → `cd <仓库根> && .venv/Scripts/python.exe launcher_mcp.py` 重启
