# 兰台核心记忆 / Lantai core memory

通过 DSH 设置中的总开关，在已配置的文件夹中自动载入兰台核心记忆。插件 0.3.0 适配 DSH 0.2.2-rc.5，要求 Node.js 24.9 或更新版本。

## 使用

1. 通过 DSH「插件 → 添加插件」安装包目录，按下文配置连接与资料归属。
2. 打开「设置 → 通用」，找到「自动载入兰台记忆」。首次安装和从逐对话授权版本升级时，开关默认关闭。
3. 开启后等界面显示「已开启并保存」。已配置文件夹里的新对话和现有对话会在下一次模型请求时自动载入。
4. 在同一设置页关闭开关可停止读取。关闭会清除活动快照并取消进行中的读取。已经写入的聊天历史和已经发送的请求保留。

记忆会随对话发送给所用模型，获准查看该对话的人可能看到资料。子代理没有独立读取资格，DSH 仍可能把父对话历史传给子代理。

## 连接与资料范围

支持 HTTPS 和基于已有 OpenSSH 配置的加密通道。显式开启本机开发选项后可以连接环回 HTTP。SSH 通过严格主机验证，经 `-W` 连接服务器自己的环回接口。传输在本次读取结束后退出。

每个工作目录绑定 `namespace`、`userId` 和 `tenantId`。默认选择 `policy` 和 `identity`，在单个目录配置中显式启用后可读取 `task`。读取会核验响应的身份、格式、版本和大小。归属不明、资料重复、坏编码和超限会结束当次读取。

优先使用已有 DSH 凭据引用，授权与工作目录核验通过后可使用已配置的本地连接文件。密钥仅用于当次读取。

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
        userId: actual-memory-owner
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

回退时先在设置关闭自动载入，再用官方插件管理器关闭「兰台本机连接配置」与「兰台核心记忆」。

## 修订

- **0.3.0** 将逐对话授权搬到设置，按当前 DSH 配置保存选择。已配置文件夹里的主对话按需自动载入。
- **0.2.3** 允许切换到已配置目录的现有对话重新授权。
- **0.2.2** 保留 Windows OpenSSH 启动所需的系统配置目录变量。
- **0.2.1** 适配 DSH rc.5，增加会话许可、加密 SSH 读取和会话内凭据复用。
- **0.1.1** 增加目录变化后的快照清理和生命周期验证。
- **0.1.0** 早期隔离读取原型。
