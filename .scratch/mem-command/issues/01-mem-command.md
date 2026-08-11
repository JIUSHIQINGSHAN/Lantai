# 01 - mem: 会话指令（MCP 命令式维护工具）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory MemoryProxy `mem-command/`（mem:sync / mem:create-skill /
mem:help），给兰台加「命令式记忆维护」：Agent 需要时主动触发维护动作（刷新注入资产、
沉淀 Skill），不依赖自动流程的时机。兰台以 MCP 命令式工具落地同构语义。

## 范围

- `lantai/services/mem_command.py`：mem_help（纯函数）/ mem_sync（scene 增量聚类补跑 +
  今日 digest 重算）/ create_skill（结构化 Skill 资产落库）
- `scripts/mcp_server.py`：注册 mem_help / mem_sync / mem_create_skill 三个工具
- 文档：ADR-0014、CHANGELOG、借鉴报告、MCP 客户端矩阵

## 验收

1. `mem_help` 纯函数有不 mock 冒烟测试
2. `mem_sync` 真实 SQLite：scene 补跑 + digest 落盘，子步骤异常不阻断
3. `create_skill` 落库为 skill 资产（procedural + structure.steps），幂等去重，
   校验失败不落库（宁 miss 不脏写）
4. MCP tools/list 18 个；mem_create_skill 参数校验错误码 -32602
5. 全量 pytest 绿

## 相关文件

docs/adr/0014-mem-command.md（本票产物）、lantai/services/mem_command.py、
scripts/mcp_server.py、tests/test_mem_command.py、tests/test_mcp.py、
docs/research/tencentdb-agent-memory-borrow.md

## Comments

## Answer（2026-08-11 已实现，test_mem_command.py 5/5 + test_mcp.py 27/27 全绿）

- 腾讯拦截 `mem:` 前缀（parser.ts + 三条命令）；兰台 MCP 即命令入口，工具名 mem_*
- `mem_sync`：SCENE_LAYER_ENABLED 时 `assign_unassigned()`（复用 scene 票增量聚类）
  + `run_digest_once()` 重算今日报告；子步骤 try/except 只记日志（宁 miss 不脏写）
- `create_skill`：零 LLM 结构化直落——memory_type="skill" + structure{name,description,
  steps} + decay_class="procedural"（永不衰减）+ lane="general"；内容 sha256 幂等去重；
  进向量库 + FTS5，可被 shell_hook 以 ## Skill 块注入
- MCP：mem_help / mem_sync / mem_create_skill（name/steps 必填，非法 → -32602）
- 测试：mem_help 纯函数不 mock；create_skill/mem_sync 真实内存 SQLite + mock embed/
  向量库/digest 输出目录；test_mcp.py 工具数 15→18 + tools/call 用例
- 文档：ADR-0014、CHANGELOG、借鉴报告、MCP 客户端矩阵（15→18）