# 兰台核心记忆 / Lantai core memory

独立的 DeepSeek Harness Host bundle，为明确绑定的新建或恢复主会话读取兰台 `CoreMemoryBlock`。兼容范围为 DSH **0.2.2-rc.3**、Node.js **24.9+**。默认关闭；不依赖按轮召回插件或 MCP 桥。

## 能做什么

- 创建/恢复时限时读取一次 `GET /core-memory?namespace=...`，默认装载 `policy`、`identity`；每个绑定可显式启用 `task`。
- 在当前代理的运行时上下文呈现带来源、块类型和版本的历史数据。普通轮不重复访问网络；宿主负责未变化快照去重和压缩后的重新呈现。
- `lantai_core_status`：查看加载状态、原因码、块数、UTF-8 字节数与版本。工具结果没有记忆正文或凭据。
- `lantai_core_refresh`：显式重读当前绑定；成功后下一模型步骤看到新版本，失败后旧快照失效。两个工具的参数都是 `{}`。
- 子代理不进行独立装载；普通分叉按其自己的绑定初始化。各代理独立缓存，不在磁盘保存正文缓存。子代理仍可能继承父会话已有历史，这项策略不负责剥离宿主传入的历史。

不自动搜索、上传对话、摄取记忆、写检查点或更新核心块。不读取本地画像文件或 `PersonaProfile`；三者是不同数据源。核心版本号不是密码学签名，本插件不宣称验证石室锁或摘要。

## 安装和配置

在 DSH 的 **Plugins → Add plugin** 选择此包目录，初次安装保持默认关闭。本包没有安装脚本，所声明的 Schemastery 和 DSH tools peer 由兼容宿主提供。它是 Host 插件，**没有自定义 Configure 按钮或配置表单**，一次性配置通过当前 profile 的用户覆盖文件完成。

1. 在该 DSH 实例的 PowerShell 终端执行 `$env:DSH_PROFILE_DIR`，确定当前 profile 目录；打开该目录已有的 `cordis.patch.yml`。不要使用仓库目录中的同名文件，也不要修改 profile 的 `package.json`。
2. 将下面的覆盖行添加到现有 YAML 列表末尾。若已有 `id: lantai-core-memory` 的用户覆盖行，只更新那一行的完整 `config`，不要重复添加同名行；保留其他插件、注释和表达式。将示例地址、目录、userId 替换为实际值。
3. 凭据按下文保存到 DSH 凭据服务后再设 `enabled: true`。保存并确认插件管理器显示配置已应用、无激活错误；若当前发行版未热应用或显示需要重启，等现有任务结束后重启 DSH。
4. 新建会话，调用 `lantai_core_status`；应看到 `ready` 或符合实际数据的 `empty`。再检查该会话首个模型请求中的 `lantai:core-memory` 上下文。已经运行的代理不追补注册。

完整用户覆盖行（不是替换整个 profile 文件）：

```yaml
- id: lantai-core-memory
  name: '@lantai/dsh-core-memory'
  config:
    enabled: true
    baseUrl: https://your-lantai.example/
    apiKeyEnv: LANTAI_DSH_CORE_TOKEN
    allowLoopbackHttp: false
    timeoutMs: 1000
    maxResponseBytes: 131072
    maxContextBytes: 16384
    bindings:
      - workspace: 'D:\Projects\your-project'
        namespace: default
        userId: your-lantai-user-id
        tenantId: null
        includeTask: false
```

以后关闭时将此行的 `enabled` 改为 `false` 并确认应用；卸载用插件管理器。用户覆盖层与包自带的默认关闭 patch 分开保存，升级包不应通过覆盖整份 profile 来迁移配置。开发验收仅使用独立运行时，不自动修改上述日常配置。

- `baseUrl` 是实际兰台服务的 API 根地址。可含路径前缀，禁止 URL 用户名/密码、query、fragment 和重定向。
- 通过 DSH 凭据服务保存 `apiKeyEnv` 指向的令牌；配置中只填引用名，不填令牌值。每次初始化/刷新重新解析凭据。请使用用途受限的服务端身份；本插件仅发 GET，不意味着该凭据本身没有写权限。
- `userId` 必须是接口返回记录的确切属主。`tenantId: null` 表示只接受明确为 null 的 tenant 字段，不能理解为任意租户。
- 每个 `workspace` 必须为绝对目录，精确匹配当前会话的归一化目录；Windows 忽略大小写。不是子目录通配，也不把符号链接折算为授权。一个工作区最多一个绑定。
- 绑定在代理初始化时固定。只有成功切换到不同目录后，旧快照才会失效；切换失败、取消验证或仍停留在原目录时，保留原快照。真正离开后再回到原目录仍需要显式刷新。切换到另一个已配置项目请新建会话，避免沿用原项目的身份/任务。
- `namespace` 用于选择核心块，**不是权限边界**。不能仅靠给两个项目取不同名称保证服务端隔离。
- 只有确认该任务块适用于这个项目时才启用 `includeTask`。它不会根据当前对话自动挑选任务，也不会读取“全局最新会话检查点”。
- 仅开发环回测试可将 `allowLoopbackHttp: true` 与 `http://127.0.0.1:PORT/` 或 `http://[::1]:PORT/` 组合。`localhost` 等 DNS 名不纳入 HTTP 例外。

地址、凭据、身份及核心数据应由维护者核对。本插件不会自动把无属主旧记录认领给你，也不提供从对话正文推断身份的路径。

## 预算、失败与刷新

| 配置 | 默认 | 范围 |
|---|---:|---:|
| `timeoutMs` | 1000 ms | 50–10000 ms；凭据解析和网络共享总预算 |
| `maxResponseBytes` | 131072 | 1024–1048576；按实际解码响应流字节计数 |
| `maxContextBytes` | 16384 | 256–65536；完整 UTF-8 数据帧预算，包含说明与元数据 |

响应超限、上下文超限、坏 JSON/UTF-8、归属不符、同类型多候选时，整次读取失败，不截断、摘要、自动选一份或修改历史。接口可能返回未选择的 task 块；本插件不会把它呈现给模型。

状态 `ready` 表示成功读取并注册快照，`empty` 表示没有非空选定块，`not_bound` 表示当前代理没有适用绑定。上下文遵守 DSH 的可选运行时上下文开关；关闭该开关的预设不会被本插件强行覆盖，实际模型可见内容以对应请求和会话日志为准。`unavailable` 的 `reason` 提供固定原因码，例如：

- `timeout`、`cancelled`、`scope_changed`
- `missing_credentials`、`credential_error`、`authentication_failed`
- `ownership_mismatch`、`ambiguous_blocks`、`invalid_response`
- `response_too_large`、`context_too_large`、`remote_error`、`transport_error`

失败不阻止聊天启动，模型上下文会明确标记未加载。每次刷新开始先使旧快照失效；后发刷新取代先发刷新，被取代的调用报告 `superseded`。目录变更、取消或卸载后的迟到响应不能重新装载。

## 数据流与留存

正文是带固定数据围栏的历史用户数据，以 JSON 编码转义 `<`、`>`、`&` 并禁止模板插值，不能提升为宿主系统指令。围栏是纵深防护，不是对模型行为的绝对保证。

**装载内容将进入 DSH 会话历史及后续模型请求。** 共享或远程访问该会话的参与者可能看到历史和相关回复；请只为适合这些参与者的数据配置绑定。插件仅发送 namespace 和认证头给兰台，不发送当前对话、工作目录或 DSH 会话标识。兰台/代理层仍可有普通访问日志。

关闭或卸载会撤销监听器、工具和上下文注册，取消未完成读取；停止后续装载，**不会删除已经持久化的 DSH 历史，也无法撤回已发给模型的内容**。远端修改在新建/恢复会话或显式刷新后生效，不向全部打开的对话即时推送。删除/撤回重要信息后应刷新相关会话，并按 DSH 自身规则处理历史。

## 开发验证

仓库根目录运行（普通单测不需要 DSH）：

```powershell
node --test integrations/dsh-core-memory/test/core.test.mjs integrations/dsh-core-memory/test/session.test.mjs integrations/dsh-core-memory/test/package.test.mjs
```

在安装 DSH 的开发环境运行真实 Cordis、AgentLoop、Session、运行时上下文与 JSONL 恢复验证：

```powershell
.\scripts\verify_dsh_core_memory.ps1 -RuntimeOnly
```

运行 Python 真实核心服务/路由契约测试（项目虚拟环境依赖需已安装）：

```powershell
python -m pytest tests/test_dsh_core_memory_contract.py -q
```

所有 fixture 使用合成内容及临时目录/数据库；测试不应连接生产实例。测试结果、实际执行命令、完整链路和 UI 验证范围见仓库交付报告。源码与会话验证不等于插件已安装到日常 profile，也不等于浏览器图标和设置页已完成视觉验收。

图标为本项目原创 SVG，无外部字体、脚本或图片依赖。

## 修订记录

### 0.1.1（2026-11-20）

- 修正目录切换行为：同目录、失败和取消验证保留适用记忆，实际切换后才清理。
- 增补真实 DSH 读取中取消、初始化卸载、超时以及会话/插件同时释放的验证。
- 配套发布检查显式要求兼容的 Node，避免把环境缺失导致的测试跳过误记为已覆盖。

### 0.1.0（2026-11-19）

- 首版只读核心记忆加载、项目绑定、显式刷新与状态查询。
