# 兰台核心记忆 / Lantai core memory

为 DSH 的指定对话读取兰台长期规则与个人资料。**默认不读取：软件主人须在该对话中点击“允许记忆”。** 当前适配 DSH **0.2.2-rc.5**，Node.js **24.9+**；插件版本 **0.2.1**。

## 现在怎么用

1. 通过 DSH **插件 → 添加插件** 安装包目录，由维护者配置连接及资料归属。
2. 刷新现有 DSH 页面。已建立的对话旁会出现记忆状态和“允许记忆”按钮；新会话尚未建立时，先发一条消息，再点允许。
3. 点击“允许记忆”后，只有当前对话可以读取所配置的资料。成功显示“记忆已载入”；没有可读资料时明确显示空状态。
4. “更新记忆”重新读取当前版本；“停止”取消后续读取。网络故障导致停止结果不明时，保留停止入口并提示重试，不假称已经关闭。

未经许可的首条请求不会带入记忆。授权后，后续模型步骤使用已装载的快照。原地压缩保持同一许可；恢复、分叉、新建或实际更换工作目录需要分别允许。许可最长默认8小时，到期主动取消进行中的读取；不会自动替你续期。

**允许后，指定资料会发送给当前对话所用的模型，并进入聊天历史。停止或卸载不能删除已经生成的历史，也不能撤回已经发送的请求。** 子代理不获得独立许可，但宿主可能把父会话历史传给子代理。

## 连接与资料范围

支持两种传输，失败时不会降级到公网明文：

- HTTPS（只对明确的本机开发接口允许环回 HTTP）。
- 使用已有 OpenSSH 配置的加密 stdio 通道。严格核验已知主机，以 `-W` 连接服务器自己的回环接口；不运行远端 shell、不建立本地监听口、不保留常驻隧道。

每个工作目录明确绑定 `namespace`、`userId` 和 `tenantId`，默认读取 `policy`、`identity`；`task` 只有明确启用时才读。namespace 是资料选择条件，不是用户权限。无属主旧记录、归属不匹配、同块多份候选、坏编码或超限均整次拒绝；不截断、不自动认领或修改资料。

已有 DSH 凭据引用优先。可选的本地旧连接文件仅在用户明确允许且 scope 验证通过后读取；匹配固定服务器与端口，密钥只暂存在当前已授权对话的内存，撤销/到期/释放即清理。插件不把旧密钥复制到源码、配置、命令行或日志，不写入新的持久凭据记录。

## 运维配置

这是 Host 和 Client 同包的可安装 bundle。operator 应通过官方插件管理入口配置安装；部署特定的非秘密连接参数可放在独立、本机的配置 bundle 覆盖层中。不要覆盖整个日常 profile，不要将密钥提交到 Git。

SSH 模式配置示例：

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
    grantTtlMs: 28800000
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

`credentialFile` 可省略；填写时只接受明确的本地绝对路径，JSON 的 `host`、`port`、`api_key` 必须与固定目标一致。其路径不是密钥本身。HTTPS 模式用 `transport: direct` 和 `baseUrl: https://your-server/`，省略SSH配置。

读取总预算50–10000毫秒，包含凭据等待与传输；响应1024–1048576字节；完整上下文256–65536个UTF-8字节。输出预算计入固定围栏和版本元数据。

## 权限与可观察状态

Host 在实际 Remote 方法中独立核验 owner Peer，许可绑定精确的 live Agent、工作目录和记忆身份。模型、wire参数、同名session字符串、普通问题答案和命令的 `source.kind=user` 均不能签发许可。缺身份或许可时，解析凭据和访问网络之前拒绝，不注入记忆正文。

模型仅有 `lantai_core_status` 与 `lantai_core_refresh` 两个工具。它们返回状态、原因、块数、大小和版本，没有正文或密钥；刷新不授予或延长权限。用户控制面另有允许/停止/状态入口，模型工具不能调用该入口完成授权。

固定历史数据说明、JSON可逆转义和 `interpolate:false` 防止资料成为模板表达式。围栏是纵深保护，不是对模型行为的绝对保证。`ready` 表示已读取并注册快照；模型实际可见内容仍遵循宿主上下文开关。

## 验证与发布范围

普通Node测试：

```powershell
node --test "integrations/dsh-core-memory/test/*.test.mjs"
```

真实安装版DSH/Gateway测试（独立合成状态，不加载日常profile）：

```powershell
.\scripts\verify_dsh_core_memory.ps1 -TimeoutSeconds 120
```

客户端只在显式构建时生成：

```powershell
node scripts/build_dsh_core_memory_client.mjs
node scripts/build_dsh_core_memory_client.mjs --check
```

带真实兰台临时SQLite后端的选配验证：设置 `LANTAI_DSH_RUN_REAL_RUNTIME=1`，运行 `python -m pytest tests/test_dsh_core_memory_contract.py -q`。生成资源同步和静态检查不等于实际浏览器挂载或视觉验收；没有浏览器控制时会明确报告这部分未验证。

本轮只做本地安装和接入，不发布远程版本或改动旧MCP、云端资料、其他任务工作树。移除本地连接覆盖bundle并关闭本插件即可回退；无需改旧桥。图标为本项目原创SVG。

## 修订

- **0.2.1**：适配当前DSH rc.5，明确owner会话许可、加密SSH读取、会话内凭据复用和允许/停止界面；未经许可不读取。
- **0.1.1**：目录实际变化后的快照清理及生命周期验证（旧版本不具备新的会话许可保护，不作为当前启用入口）。
- **0.1.0**：早期隔离读取原型。
