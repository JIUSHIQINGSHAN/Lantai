# DSH 自动召回集成诊断（2026-11-19）

**处置：停用原型，不发布自动召回能力。** 公开的 [DSH Host bundle](<../../integrations/dsh-lantai/README.md>) 默认无操作；显式启用会在注册监听器、访问凭据、读取路径或请求网络之前失败。需要检索时，显式调用已配置的兰台 MCP `search`，不改变现有 MCP 或 DSH profile。

## 范围和证据口径

核对已安装的 DSH Desktop 发行版 `@deepseek-ai/dsh` **0.2.2-rc.3**（安装包 `package.json:2–4`）和当前仓库的兰台服务实现。下表代码坐标均相对于已安装的包目录，不引用机器绝对路径。Host 源码可证实接口语义，不能据此断言当前 profile 已加载本草案、或任何远端兰台实例与仓库版本一致。审查只用了源码与合成数据探针，未安装、启用或连接生产端点。

| 契约 | 已安装源码证据与实际含义 |
|---|---|
| `agent/pre-step` | `@deepseek-ai/dsh-agent/src/runtime-types.ts:138–146,339–351`：waterfall 输入是已从 inbox 取出的消息和 turn/step/signal/modelSelection；允许以 `enter` 替换批次或 `reject`。`@deepseek-ai/cordis/src/events.ts:235–269`：外层先运行，可在 `await next()` 后修改返回值；注册属于 fiber。 |
| 首步及输入来源 | `@deepseek-ai/dsh-agent-loop/src/inbox.ts:103–112`：首步把 `next-step` 项和最多一条 `next-turn` 项合并。`@deepseek-ai/dsh-agent-loop/src/agent.ts:171–190,289–318,348–367`：空闲 `steer` 可开轮，`inject` 不唤醒。`@deepseek-ai/dsh-agent/src/runtime-types.ts:320–329` 的 claim 通知没有 `target`。 |
| Web 和 SDK 归属 | `@deepseek-ai/dsh-api-session-controller/src/index.ts:467–491` 在 Host 端为非空 Remote prompt 构造 `kind: user`，记录请求 ID 和非 owner 的 principal；同包 `src/types.ts:323–331` 说明请求 ID 是客户端生成的；`src/commands.ts:364–374,483–512` 显示 queue 和 steer 使用同样来源，现有队列项转为 steer 也保留原消息。`@deepseek-ai/dsh-typert-protocol/src/principal.ts:8–17`：owner 或受信进程内调用均不记录 principal，缺字段不证明真人。`@deepseek-ai/dsh-sdk-jsonrpc-server/src/server.ts:262–280`：程序客户端同样生成 `kind: user`。 |
| Session 和目录 | `@deepseek-ai/dsh-session/src/types.ts:28–30,95–128`：`SessionId` 是品牌字符串，不承诺 ASCII/长度，历史普通 fork 可能保留 `parentSession`，子代理有独立的 `origin`/深度信息。`@deepseek-ai/dsh-working-directory/src/index.ts:136–143,188–193,217–233`：`get(session)` 读取已提交有效目录，`set` 可另行提交变更；没有给读取者返回外发租约。 |
| 凭据 | `@deepseek-ai/cordis/src/reflect.ts:239–256`：`ctx.get` 可选查找活动服务。`@deepseek-ai/dsh-credentials/README.md:46–61`：`resolve` 返回 `{ value, source }` 或 `undefined`。`@deepseek-ai/dsh-credentials-local/src/index.ts:571–584,638–646`：已存值优先，向下解析启动进程环境和项目/用户环境文件。 |
| 日志和模型输入 | `@deepseek-ai/dsh-agent-loop/src/agent.ts:441–485`：先准备模型请求，再将已准入的消息写入 `user/message`，然后构建模型请求。`@deepseek-ai/dsh-llm/src/message.ts:86–108` 包含 `recall` form；`@deepseek-ai/dsh-session-format-v3-to-v4/src/message-sources.ts:7–22` 可保留生产者自有来源；`@deepseek-ai/dsh-session-log-deepseek/src/index.ts:89–106` 会序列化 `user/message` 的 data 供已配置的会话日志传送使用。 |

## 原型风险与最小复现

1. **P1：来源和准入不可靠。** 向允许工作区的空闲会话提交单条 `steer`，或由获授权的非 owner Remote peer 提交一条文本，会得到原型接受的消息形状。真人 `queue` 与 `inject()` 共批反而被长度检查跳过。注册两层真实 Cordis listener，外层在 `await next()` 后拒绝，原型位于内层，此时原文可能已发送。后续模型准备失败也不撤回 HTTP。应由可信 Host 证明此次确为授权的 `next-turn` 且拥有最终准入；过早发送后再检查结果不成立。
2. **P1：工作区入口和兰台结果授权是两回事。** 两个工作区共享同一凭据就共享同一服务端身份。`domain: user` 不按工作区隔离。仓库的 `/search` 使用 [Bearer 主体](<../../lantai/core/auth.py#L119>) 和[归属及域过滤](<../../lantai/retrieval/hybrid.py#L646>)，兼容无属主旧数据；闸门放行后 [检索事件](<../../lantai/api/routes_search.py#L76>) 默认[保存查询原文和 session ID](<../../lantai/observability/retrieval_log.py#L99>)。原型把会话的目录在异步取凭据前做快照；延迟解析时切换目录或替换路径可使快照失效。真路径查询不验证目标是目录，也不能固定其在请求送达时的身份。
3. **P2：传输边界存在未覆盖的歧义。** 运行时确认 `127.1` 规范化为 `127.0.0.1`，并未访问非环回地址，但原文限制与描述不一致。实际响应流有 256 KiB 上限和严格 UTF-8 解码，仍可能因[通用接口返回的完整结果](<../../lantai/retrieval/hybrid.py#L951>)过大而整轮弃用。ASCII 会话 ID 规则有利于头部传输，但不是 DSH 规则。另需覆盖路径折叠、重定向、压缩、编码、正文和转义后长度、取消/卸载发生在异步读取中的情况。
4. **P2：结构匹配不等于结果可信。** 服务端的[闸门](<../../lantai/gate/prefilter.py#L155>)是启发式判断，证据由[相同结果里提取前缀](<../../lantai/retrieval/evidence.py#L18>)；一个无关记录也可满足内部匹配检查。原型敏感标记不是当前 [`MemoryItem` 模型](<../../lantai/models/tables.py#L107>)的正式契约，关键词筛查不保证敏感数据不会进入 DSH 日志或模型。软件取消只能停止本地等待和尽量停止流，不能撤回已经收到、传输或提交的内容。

## 当前验收与未来闸门

- 停用验收：一组测试直接调用真实 bundle 入口，断言缺省/关闭配置不执行任何操作，显式启用以固定错误失败。以哨兵监测 Context、配置读、所用文件 API、凭据环境和 Fetch。回归测试在原型上因读取配置失败，停用入口上通过。私有归档逐文件校验 8 个原件的 SHA-256，包含原测试和显示资源。
- 恢复研究前：先明确是 owner Web 新排队输入还是逐次授权。确认实际 `next-turn` claim（不是仅确认首步；若从 inbox 日志推导，必须绑定被删除项的 ID 和 target），并确保检索插入点位于最终准入后的可信 Host 边界。若不能满足，保留显式 MCP 方式。
- 功能验收：用真实 Cordis、AgentLoop、Session 和隔离兰台数据，验证普通 owner queue、非 owner、idle steer、队列转 steer、混合注入、child/fork、暂停/恢复以及外层和内层 veto；再验证工作区/凭据隔离、异步切换和撤权、上下文来源和回放、受限响应协议与留存。原型的极小 Cordis stub 未覆盖这些契约。

停用路径测试通过不表示真实 DSH 运行时安装、启用、UI 展示或自动召回行为已验收。
