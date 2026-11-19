# ADR-0090: 主干工作区修复的兼容与外发边界

Date: 2026-11-19  
Status: Accepted

## Context

旧工作区基于 0.68.0；当前主干已包含 0.69.0 的运行时、逐调用归因、独立自动召回实验以及远程 MCP 桥。迁移修复必须保留主干新增能力、schema 与依赖。本决策修订 ADR-0089 的召回启用条款，保留其独立适配和传输约束。

## Decision

1. **保留服务端及逐调用归因适配。** 不修改 `integrations/dsh-lantai/` 的 MCP 调用身份和元数据语义，不回退 schema、UTC 日期类型或锁定依赖。
2. **只暂停独立自动召回包的启用。** `integrations/dsh-lantai-recall/` 缺省无操作；显式 true 在任何目录、凭据、事件或网络副作用前失败。图标与包标识保留。停止自动外发不要求删除已有归因事件、会话或历史记录。
3. **桥接裁决参数与现有服务一致。** `conflict_resolve` 要求非空白理由，decision 仅为 resolved/dismissed；在桥边界以参数错误拒绝服务本来就不接受的调用。
4. **新增明确的记忆历史工具，同时兼容旧接口。** `list_memory_checkpoints(memory_id, limit)` 返回 `{checkpoints: [...]}`。`latest_checkpoint` 的无参、session_id、memory_id 和 limit 行为均保留；其说明引导新调用使用独立历史工具，不移除已存在参数。
5. **保留平台和环境边界。** 浏览器回归使用真实 Chromium 和合成同源 API；不承诺原生 OS 输入法或未测浏览器。只在相应 SDK 预算测试内隔离宿主代理环境，不改变生产网络设置。wheel 测试验证桥接安装入口及 UI 资源。
6. **评测服务必须来自已创建的进程。** 先验证隔离目录与显式凭据，再启动；就绪不可由固定端口上的其它服务证明。回收只作用于持有的自有进程树，兼容 Windows 虚拟环境的启动器/解释器层级，不扫描并终止任意同端口进程。

## Evidence for suspending recall

已安装 DSH 的 `dsh-agent-loop/src/inbox.ts` 中 `claim()` 合并 next-step 与 next-turn；首步不等于新排队输入。`cordis/src/events.ts` 的 waterfall 外层先运行，外层可在 `await next()` 后替换或否决内层结果。现有原型在 pre-step 内发出请求，不能以 `step === 1`、`kind: user` 或内层 decision 证明最终准入。

工作区 allowlist 限定请求发起位置，不改变同一 Bearer 身份可检索的记录集。重新启用须通过可信 Host 归属与最终准入边界、数据范围及留存约定，并用真实宿主组合和隔离服务验证；当前停止路径测试不替代该功能验收。

## Compatibility and rollback

只修改自动召回包，不覆盖较新的归因适配包。显式 MCP 工具及旧检查点参数继续可用。召回原型仍可从原基线 Git 对象追溯，私有原件也保全。无需改用户 profile、凭据、业务库或已推送标签；本地候选不代表部署或发布。

## Verification

真实 stdio/环回 HTTP 测试覆盖桥接非法输入无转发、合法裁决、历史工具及旧参数兼容。Node 测试对停用入口设置副作用绊线。独立适配包沿用现有安装版隔离测试；所有具体执行结果在候选验证台账记录。
