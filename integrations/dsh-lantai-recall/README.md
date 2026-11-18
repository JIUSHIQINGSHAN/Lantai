# 兰台召回 / Lantai recall

这是默认关闭的 DeepSeek Harness Host bundle。启用后，它只在允许的工作区为主代理的真人文本尝试一次自动召回。它不注册 MCP 工具，不自动写入对话、记忆或检查点。兰台 `/search` 仍会产生正常的检索事件。自动召回、手动 MCP 工具和 DSH 自身会话恢复彼此独立。

## 启用前先确认

- 运行环境：DeepSeek Harness `0.2.2-rc.3` 的 Host。先使用测试兰台实例和隔离 DSH 会话；不要把个人旧库用作验收数据。通过 DSH 的 **插件 → 添加插件** 安装此本地目录；安装后仍为关闭，需显式配置才会发送查询。此仓库不会修改已运行的 DSH profile。
- 使用 HTTPS 与专门的兰台 Bearer key。凭据仅通过 `credentialRef` 引用，不把值放入配置和文档。进程内存在 DSH 凭据服务时，由该服务解析；没有时才读取同名启动环境变量。环回 HTTP 仅可显式用于本机测试，不能用于旧公网明文端点。
- 自动召回会**向配置的兰台端点发送选中的真人文本**。返回的相关历史摘录会写入 DSH 已提交的会话记录，并可能传给该会话使用的模型服务；关闭插件或在兰台撤回记忆不会清除已写入的 DSH 历史。敏感字样拦截只是保守辅助，不能代替工作区、库和凭据隔离。

## 配置

在 DSH 的配置界面或管理员批准的部署层覆盖 `lantai-recall` 行的完整配置。示例中的域名和目录是占位符；不要贴真实密钥。

```yaml
- id: lantai-recall
  config:
    enabled: true
    baseUrl: https://lantai.example.invalid
    workspaces: ['D:/Projects/example']
    credentialRef: LANTAI_RECALL_BEARER_TOKEN
    timeoutMs: 500
```

| 配置 | 意义 |
|---|---|
| `enabled` | 默认 `false`；显式 `true` 才安装轮次监听器 |
| `baseUrl` | 兰台 REST 根地址，不是 MCP URL；插件追加 `/search` |
| `workspaces` | 明确允许的绝对工作区；原始与当前工作目录必须指向同一个已允许的真实目录；不会自动授权整个子树 |
| `credentialRef` | Bearer key 的 DSH 凭据引用或启动环境变量名，默认 `LANTAI_RECALL_BEARER_TOKEN` |
| `timeoutMs` | 默认 500，可配 50–2000；覆盖路径校验、取凭据、请求和读取响应 |
| `allowLoopbackHttp` | 默认 `false`；仅显式 `true` 时允许 `127.0.0.1`/`::1` 明文 HTTP |

## 验收与回滚

1. 在测试端点准备无敏感信息的唯一记忆。打开允许的工作区，在新会话问与该记忆相关的问题，不要求调用记忆工具。对照模型输入或会话记录，应出现标明来源的历史摘录。普通 问候、无命中、超时、401 或 503 不应生成额外消息，也不应打断对话。
2. 在另一工作区或关闭开关后复测，不应有自动请求。主代理通过现有 MCP 主动检索的能力不受插件开关影响。
3. 停用此 bundle 或将 `enabled` 改回 `false`。不需要删库、清理日志、重启兰台或改变现有 MCP 配置。

首版每轮只取最多 3 条可归因的摘录，不使用全文/画像、不重排、不复用旧查询、不监听自动写入事件。有新请求但缺少可信会话标识、无效配置或缺凭据时宁可不召回。测试：`node --test test/recall.test.js`（Node 22+）。
