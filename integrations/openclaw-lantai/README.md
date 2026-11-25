# OpenClaw 兰台入口

本目录提供原生 OpenClaw 插件 `lantai-memory`。不增加依赖、不替换现有 memory slot，
不新增聊天传输渠道。宿主通过清单发现入口并校验配置，插件通过 `api.on` 登记
`before_prompt_build` 和 `agent_end`，只消费已存在的渠道与明确允许的会话。

## 接入

将本目录绝对路径追加到 OpenClaw 已有 `plugins.load.paths`，将 `lantai-memory`
加入已有插件允许列表，并合并以下入口配置。保留其他插件和配置。
示例文件路径必须换成本机实际路径；本模块不写宿主配置、不代装或重启。

```json
{
  "plugins": {
    "entries": {
      "lantai-memory": {
        "enabled": true,
        "config": {
          "python": "D:/Tools/lantai/Scripts/python.exe",
          "shellHook": "D:/src/Lantai/scripts/shell_hook.py",
          "userId": "default",
          "agentId": "main",
          "channels": ["webchat"],
          "sessionKeys": ["agent:main:main"],
          "captureDialogue": false
        }
      }
    }
  }
}
```

Python 环境应与兰台锁定依赖一致，并已经配置 shell hook 必需的 `API_KEY` 和专用
`LANTAI_HOME`。凭据使用现有环境配置，不写进插件清单。路径必须为存在的绝对文件路径；
属主、智能体、渠道与会话均须明确配置。空白、控制字符、通配符、重复条目、
未知字段和错误类型会被拒绝。渠道和会话列表各最多 128 项。

运行时必须提供 `agentId`、`channel`、`sessionKey`、`sessionId`、`runId`；
缺失或不匹配时不启动子进程、不查询、不写入。cron、subagent、心跳等非用户触发，
以及宿主标记为 `inter_session` / `internal_system` 的输入均跳过。此插件只适用于
显式允许的本地单属主会话；`userId` 是检索属主配置，不代表全套多租户鉴权。
冻结 shell hook 的常驻画像仍含本机文件和全局画像，不能拿本插件隔离共享宿主的不同用户。

## 记忆桥接

- 懒启动 `python shell_hook.py --serve`，固定子进程宿主为 `openclaw`、检索角色为
  `user`。保留配置的检索属主，禁止继承父进程的 `admin` 角色。清除 Python 路径注入变量。
- 查询优先使用原生 `currentUserMessage`，旧宿主缺此字段时只用 `prompt` 检索。
  返回 `prependContext`，逐字保留服务端围栏，不提升为系统指令。
- 回执只在相同原生运行的 `agent_end.success=true` 后发送，去重依据 ID。
  失败轮、空注入和重复终态不回执。未收到终态的事件留待服务端按原协议处理。
  不将普通运行失败冒充“用户否决”，不伪造 `retrieval_veto`。
  宿主提供注入有效期检查时，在接受查询结果前检查；失效结果不注入、不回执。
- `captureDialogue` 默认关闭。开启时只提交原生 `currentUserMessage`，
  不采集历史、投影 prompt 或助理输出；轮号为本插件实例内按会话递增的本地序号。
  **冻结 NDJSON 对话动作固定写到 `default`，因此只允许 `userId=default`
  开启此项；其他属主的读路径保留，写入配置会被拒绝。**
- 同一运行的重复 prompt 构建共用查询；同一运行原话发生变化则停止该条回执与写入。
  保留最多 128 条运行记录、5 分钟有效期，超期重新查询；会话计数最多 128 项。

子进程请求串行，队列最多 32 项，帧最多 1 MiB。查询等动作等待上限 20 秒，
对话写入为 35 秒；服务端对应墙固定为 8 / 30 秒，保留返回宽限。
坏帧、超长响应、退出、超时只中断当前交换，下次请求重新启动子进程；
失败写入不自动重试。宿主 service stop 会中断当前请求、拒绝队列并回收进程。
日志仅报通道不可用，不输出对话、凭据或完整响应。

## 验证与兼容范围

从仓库根运行 `python -m pytest tests/test_adapter_hermes_openclaw.py -q`。
Python 测试会执行 `test_adapter_hermes_openclaw_node.mjs` 原生测试，并用隔离合成数据
验证真实 SQLite/Chroma 注入与回执。仅外部 LLM/embedding 网络使用确定性替身。
需要 Node.js 22+；没有 Node 时明确失败，不跳过。

已验证原生入口调用、真实子进程与冻结兰台服务契约。未在运行中的 OpenClaw 客户端
安装、启用或重启；不同宿主版本若缺少上述原生上下文字段，会保守不执行。
应在实际启用前确认宿主允许该插件的 prompt 注入钩子。

SDK 形状核对自 OpenClaw 官方源码，核对提交
`509e2afa4ed1b721b9105bd092062d9b7a5cf0b8`：

- `src/plugins/hook-agent-context.types.ts`
- `src/plugins/hook-before-agent-start.types.ts`
- `src/plugins/hook-agent-finalize.types.ts`
- `src/sessions/input-provenance.ts`
- `docs/plugins/manifest.md`

来源仓库：<https://github.com/openclaw/openclaw>。
兰台线协议权威入口：[宿主接入协议](../../docs/host-hook-protocol.md)。
