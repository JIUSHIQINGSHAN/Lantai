# ADR-0014: mem: 会话指令——MCP 命令式记忆维护

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/mem-command/issues/01-mem-command.md)

## 背景

腾讯 TencentDB Agent Memory MemoryProxy 在代理层拦截 `mem:sync / mem:create-skill /
mem:help`（`MemoryProxy/src/mem-command/parser.ts` + `commands/*`），让用户/Agent 显式
驱动记忆维护：刷新会话注入缓存、把当前会话归档为 Skill。价值在于**命令式 UX**——
维护动作不再只依赖自动流程时机，Agent 可主动触发。

兰台现状：维护动作要么自动（scheduler/evolve worker），要么散在 REST/MCP 工具里
（scenes/rebuild、raw_add 等），缺少「一个命令刷新注入资产、一个命令沉淀 Skill」的
显式入口。调研见 `docs/research/tencentdb-agent-memory-borrow.md`。

## 决策

| 项 | 决策 |
|----|------|
| 接口形态 | MCP 命令式工具（mem_help / mem_sync / mem_create_skill），不照搬代理层 `mem:` 前缀拦截——兰台 MCP 即命令入口，工具名即命令名 |
| mem_help | 纯函数返回命令表 + 示例（零副作用） |
| mem_sync | 刷新注入资产 = scene 增量聚类补跑（`assign_unassigned`，SCENE_LAYER_ENABLED 时）+ 今日 digest 重算（`run_digest_once`）；子步骤异常只记日志不阻断（宁 miss 不脏写）；返回 scene/digest 明细与耗时 |
| mem_create_skill | 结构化直落（零 LLM）：memory_type="skill" + structure{name,description,steps} + decay_class="procedural"（永不衰减）+ lane="general"；内容 sha256 幂等去重；进向量库 + FTS5，可被 shell_hook 以 `## Skill` 块注入；name/steps 校验失败不落库 |
| 幂等与安全 | create_skill 同内容去重返回既有记忆；不新增设置项；不触碰既有读写路径 |

## 理由

- 与腾讯语义对齐但去代理化：兰台消费端就是 MCP，`mem_*` 工具名即命令，Agent 可显式调用
- create_skill 走 verbatim 通道同款直落（零 LLM），避免「低置信度提取」污染；procedural
  永不衰减复用既有 Skill 资产化约定（ADR-0011），shell_hook 无需改动即可注入
- mem_sync 复用两票已有能力（scene 增量聚类 + digest worker），编排薄、异常降级
- 不照搬腾讯的会话 buffer 归档（task_id/archive_key 文件体系）：兰台记忆全在 SQLite，
  结构化直落更简单且可召回