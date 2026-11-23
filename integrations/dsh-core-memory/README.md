# 兰台核心记忆 / Lantai core memory

通过 DSH 设置中的两个独立开关，在已配置的文件夹中自动载入核心记忆，或从此后的主对话用户文字提取并写入兰台。插件 0.4.0 适配 DSH 0.2.2-rc.5，要求 Node.js 24.9 或更新版本。

## 使用

1. 通过 DSH「插件 → 添加插件」安装包目录，按下文配置连接与资料归属。
2. 打开「设置 → 通用」，找到「自动载入兰台记忆」。首次安装和从逐对话授权版本升级时，开关默认关闭。
3. 开启后等界面显示「已开启并保存」。已配置文件夹里的新对话和现有对话会在下一次模型请求时自动载入。
4. 在同一设置页关闭开关可停止读取。关闭会清除活动快照并取消进行中的读取。已经写入的聊天历史和已经发送的请求保留。

记忆会随对话发送给所用模型，获准查看该对话的人可能看到资料。子代理没有独立读取资格，DSH 仍可能把父对话历史传给子代理。

「设置 → 通用 → 自动提取并写入兰台」是另一个独立开关，默认关闭，开启后会把已配置文件夹中主对话的新用户文字发送给兰台 `/dialogue/async`。不补传历史，不收集系统注入、模型回答、工具结果、附件或子代理对话。每次提交包含明确会话来源和稳定幂等键；提取、质检、待审和记忆演化沿用兰台现有机制。

关闭自动提取会立即丢弃本地排队文字并取消未完成请求；服务器已经接收的任务仍可能完成，已有记忆保留。读取和提取分别保存选择，互相切换不会改变另一项。开关关闭时提交的待处理文字、恢复的旧队列、跨开关或目录变化的旧待处理消息不会在之后被追授提取许可；编辑或移位同一消息也不会刷新许可。单会话最多排队 16 条，每条上限 50000 字符，失败不自动重试。

`lantai_extraction_status` 返回本对话自动提交的状态、固定原因码、排队数量、已接收次数和最近任务号，不返回正文。`submitted` 表示服务器已接收排队，是否形成正式记忆由后续管线决定。

常见原因码：`not_bound` 表示目录未配置；`disabled` 或 `authorization_required` 表示自动提交未获许可；`missing_credentials` / `authentication_failed` 表示凭据缺失或认证失败；`dialogue_disabled` 表示服务端未开放对话摄取；`input_too_large` / `queue_full` 表示本地长度或排队限额；`timeout` / `transport_error` 表示本次请求未取得有效回执；`invalid_response` / `response_too_large` 表示回执不合契约或超限。失败不会自动重试；请求超时或被取消也不能证明服务器从未接收，已接受任务仍可能继续完成。界面的“重新查询”只查询设置保存结果，不补发旧对话。

## 连接与资料范围

支持 HTTPS 和基于已有 OpenSSH 配置的加密通道。显式开启本机开发选项后可以连接环回 HTTP。SSH 通过严格主机验证，经 `-W` 连接服务器自己的环回接口。传输在本次请求结束后退出。

每个工作目录绑定 `namespace`、`userId` 和 `tenantId`。默认选择 `policy` 和 `identity`，在单个目录配置中显式启用后可读取 `task`。读取会核验响应的身份、格式、版本和大小。归属不明、资料重复、坏编码和超限会结束当次读取。

API Key 模式的服务端用户身份固定为 `api_key`，示例因此使用 `userId: api_key`、`tenantId: null`，正文前沿用既有管理员元数据检查。其他个人用户须使用对应的 Bearer 凭据，并使 `userId`、`tenantId` 与服务端验证的实际身份匹配；Bearer 需要支持 `GET /dialogue/identity` 的服务端，该接口只返回真实用户和生效租户，不读取记忆。旧服务端缺接口时返回固定原因 `identity_unavailable`；归属不匹配时为 `ownership_mismatch`，均不发送正文。新接口拒绝开发模式、混合认证头和非用户角色，已有鉴权与写接口不变。提交前预检查使用同一连接、凭据和租户，不包含对话正文；它与提交不是同一数据库事务，依赖相同凭据的归属不被并发重绑且网关配置一致。非空租户标识须为 1–256 个不含空格的可见 ASCII 字符，否则为 `invalid_config`。提取监听的是已被 DSH 会话内存日志接受的新用户事件，不代表会话文件已完成物理落盘。插件升级不会自动部署服务端。

优先使用已有 DSH 凭据引用，授权与工作目录核验通过后可使用已配置的本地连接文件。密钥仅用于当次请求。

## 运维配置

通过官方插件管理入口安装主插件，并用本机配置包保存连接和资料范围。先启用主插件，再启用连接配置。升级主插件后，同样最后启用连接配置。

```yaml
- id: lantai-core-memory
  name: '@lantai/dsh-core-memory'
  config:
    enabled: true
    transport: ssh
    authMode: api-key
    apiKeyEnv: LANTAI_DSH_CORE_TOKEN
    timeoutMs: 8000
    maxResponseBytes: 131072
    maxContextBytes: 16384
    ssh:
      executable: 'C:\Windows\System32\OpenSSH\ssh.exe'
      alias: existing-server-alias
      expectedHost: memory.example
      user: existing-server-user
      sshPort: 22
      remotePort: 18767
      credentialFile: 'D:\YourConfig\lantai_remote.conf'
    bindings:
      - workspace: 'D:\Projects\your-project'
        namespace: default
        userId: api_key
        tenantId: null
        includeTask: false
```

`enabled` 配置挂载已配置的插件和设置入口。软件主人的选择由 DSH 存储保存，随当前配置和资料范围生效。连接、凭据引用、文件夹绑定或记忆类别变化后，需要在设置页重新开启。旧版会话许可需要重新选择。

`credentialFile` 可省略。选用时填写明确的本地绝对路径，文件的 `host`、`port`、`api_key` 必须匹配固定目标。HTTPS 模式使用 `transport: direct` 和 `baseUrl: https://your-server/`，省略 `ssh`。

读取总预算为 50–10000 毫秒，覆盖凭据和传输。响应限额为 1024–1048576 字节，完整上下文为 256–65536 个 UTF-8 字节，计入围栏和版本元数据。

## 权限与状态

Host 核验设置请求的实际 owner Peer。启用后的每次读取核验当前主对话、精确工作目录和资料范围。工作目录切换会清理旧快照。空闲对话继续保持空闲，直到下一次请求。

`lantai_core_status` 和 `lantai_core_refresh` 返回当前对话的状态、原因、块数、字节数和版本。刷新要求总开关已开启，沿用工作目录范围。记忆以 JSON 数据加入 DSH 的运行时上下文，由宿主决定后续请求使用范围。

## 验证与回退

```powershell
.\scripts\verify_dsh_core_memory.ps1 -TimeoutSeconds 120
node scripts/build_dsh_core_memory_client.mjs --check
```

用 `node scripts/build_dsh_core_memory_client.mjs` 重新生成 Client。用 `LANTAI_DSH_RUN_REAL_RUNTIME=1` 运行 `python -m pytest tests/test_dsh_core_memory_contract.py -q` 可覆盖临时兰台 SQLite 后端。

回退时先在设置分别关闭「自动提取并写入兰台」与「自动载入兰台记忆」，等待保存确认，再用官方插件管理器关闭「兰台本机连接配置」与「兰台核心记忆」。关闭提取不能撤销服务器已接受的任务。

从 0.3.0 升级会保留范围匹配的既有读取选择；新增提取选择独立保存、默认关闭，升级不会替用户开启提取。

## 修订

- **0.4.0** 增加独立的“自动提取并写入兰台”设置，默认关闭；从主对话的新用户文字自动提交至既有摄取管线，支持停止、归属与幂等回执。
- **0.3.0** 将逐对话授权搬到设置，按当前 DSH 配置保存选择。已配置文件夹里的主对话按需自动载入。
- **0.2.3** 允许切换到已配置目录的现有对话重新授权。
- **0.2.2** 保留 Windows OpenSSH 启动所需的系统配置目录变量。
- **0.2.1** 适配 DSH rc.5，增加会话许可、加密 SSH 读取和会话内凭据复用。
- **0.1.1** 增加目录变化后的快照清理和生命周期验证。
- **0.1.0** 早期隔离读取原型。
