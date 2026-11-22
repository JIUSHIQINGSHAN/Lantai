# 显式会话原文接收与 Markdown 下载

本期提供由调用者显式提交的原文保存、查询、删除及单向 Markdown 下载。原文与唯一待处理作业在同一个 SQLite 事务提交后才确认保存，不调用 LLM、embedding 或 Chroma。

默认 `FEATURE_CONVERSATION_CAPTURE=False`。本期不接宿主自动采集，不改变 MCP 工具，不生成提取候选或生效知识，也不读写 Obsidian vault。作业为后续消费者保留；`pending` 不表示已经提取。

## 启用与认证

维护者设置 `FEATURE_CONVERSATION_CAPTURE=true` 后可使用接口。路由逐次检查开关，关闭时返回 `404`。关闭期间，请求跳过正文处理、业务写入和请求遥测。

开启后，每次请求必须携带明确凭据：库内有效 `Authorization: Bearer <key>`，或与进程配置匹配的 `X-API-Key`。真实凭据校验在读取请求正文之前完成，失败直接返回固定 `401`；无效 Bearer 即使搭配坏 JSON，也不会先读取或解析正文。已认证 Principal 在当前请求复用，不在正文解析后重新验证。这里不允许借默认 DEV MODE 无凭据进入；随意填写一个 `X-API-Key` 也不构成凭据。普通认证逻辑保持原有行为，新的收窄只作用于会话接口。

环境管理密钥使用的 `api_key` 用户身份在此接口保留。即使数据库里存在同名 `user_id=api_key` 的普通 Bearer 账户，也以 `403` 拒绝该账户访问会话接口，避免两个认证来源意外共享原文范围。

当前通用认证实现将 `X-Tenant-Id`、`X-Agent-Id` 从客户端头部读取，不能把这两个自报标签当作可信授权。本期会话 REST 因此拒绝任何非空的这两个头部（`403`）；其可访问范围为已认证 user 与空 tenant/agent。底层 MessageStore 仍按 `(user_id, tenant_id or '', agent_id or '')` 严格相等比较，admin 角色也不能跨范围。未来受信适配器若要使用非空 tenant/agent，须先建立可信身份映射，不能仅删掉头部拒绝规则。

`workspace_id`、`host_id`、`conversation_id` 和 `source_message_id` 是来源标签；工作区不同不提供新的认证或租户保证。共用同一凭据的调用者拥有同一身份。本文 HTTP 测试使用临时文件 SQLite 和合成身份，不能证明真实宿主配置或生产部署已经验收。

## 显式接口

所有路径相对于服务根地址。请求和响应均使用 UTF-8，响应附 `Cache-Control: no-store`；正文不放进 URL。

鉴权成功后，接口逐块读取实际请求流，合计上限为 **640 KiB（655,360 字节）**，超过即固定 `413`，不回显输入。此限制同样适用于没有 Content-Length 或声明长度不实的流；仅凭声明长度大不会拒绝实际很小的请求。限额足够容纳 50,000 个非 BMP Unicode 码点以 JSON 双代理转义发送（每码点 12 个 ASCII 字节）的正常请求，但与 50,000 字符正文限制是两道独立约束。超限内容不交给 JSON 解析器或 MessageStore，也不保存为原文。

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/conversation/messages` | 新建 `201`，同消息重放 `200` |
| GET | `/conversation/messages/{message_id}` | 查看本身份的消息；未知或他人消息 `404` |
| GET | `/conversation/messages` | 必须指定 `host_id` 与 `conversation_id`，有界分页查看 |
| DELETE | `/conversation/messages/{message_id}` | 擦除本条正文与哈希并取消关联作业，保留零正文身份；幂等 |
| GET | `/conversation/messages/{message_id}/markdown` | 返回 Markdown 文本附件；未知、他人或 deleted 消息 `404` |

### 提交原文

```json
{
  "host_id": "explicit-http",
  "conversation_id": "sample-conversation-001",
  "source_message_id": "sample-message-001",
  "workspace_id": "sample-workspace",
  "role": "user",
  "completion": "completed",
  "content": "  灯塔项目计划周五上线。\n保留原文空白。　 ",
  "occurred_at": "2026-11-20T15:00:00+08:00"
}
```

必填字段为 `host_id`、`conversation_id`、`source_message_id`、`role`、`content`。`workspace_id` 默认空字符串；`completion` 默认 `completed`；`occurred_at` 可缺省或为 null，表示发生时间未知。时间若提供，必须是带时区的 ISO 时间，统一保存为 UTC，不以服务接收时间替代未知发生时间。

来源标签仅允许 ASCII 字符 `A-Z a-z 0-9 _ . : -`，长度 1–128；只有 workspace 可以为空。角色仅允许 `user`、`assistant`，完成状态仅允许 `completed`、`failed`、`cancelled`、`partial`。正文不得为空或纯空白，最多 50,000 字符，合法文本的首尾空白和换行原样保留。未知字段和隐藏角色被拒绝；首期修订固定为 1，不支持原地编辑。

同一认证范围和 workspace/host/conversation/source-message 组成消息身份。相同身份及相同 role、completion、正文、发生时间重放时返回原 `message_id`；相同身份但业务字段改变返回 `409`。不同消息 ID 即使文字相同，也保存为两次发生，不按正文哈希合并发言。

成功回执示意：

```json
{
  "message_id": "<server-generated-id>",
  "status": "stored",
  "durable": true,
  "replayed": false,
  "processing_status": "pending"
}
```

`durable:true` 表示原文和待处理意图已在约定 SQLite 事务中提交，不表示提取、知识应用或语义索引已完成，也不承诺设备断电零损失。

疑似密钥形状在保存前拒收，返回固定 `422` 错误，不回显命中内容。命中注入特征的文本可隔离保存为 `quarantined`，关联作业为 `held`；保存不赋予其参与模型提示或自动执行的资格。本期没有后台提取消费者。

### 查询与删除

列表参数：`host_id`、`conversation_id` 必填；`workspace_id=''`、`limit=50`、`offset=0` 可省略。`limit` 范围 1–200，`offset` 不小于 0，按 `received_at`、`message_id` 的底层稳定 ID 顺序分页。没有无条件全库拉取入口。

单条和列表消息返回来源标签、角色、完成状态、原正文、发生/接收时间、状态、处理状态及固定 `revision:1`，不返回正文哈希或作业令牌。deleted 状态仍可查询，但正文为空。

删除把正文置空、哈希清空、关联作业取消并撤销租约/令牌。原消息身份保留；之后重放同一源消息 ID，返回 `status:deleted`、`durable:false`，不会重新保存请求携带的正文。若以后确有新的发言，使用真实的新消息 ID，不能用重放恢复已删除消息。

本期尚无派生知识，删除的是该接收层保存的原文和关联作业状态。不要由此推导旧记忆、宿主历史、手工下载文件或备份已经同步删除。后续提取阶段须另完成来源与派生内容删除传播。

## 单向 Markdown 下载

下载接口只生成 HTTP 附件，不向服务器文件系统、Obsidian vault 或任意用户目录写入文件，也不安装插件或监听笔记变化。您可自行把下载文件打开为笔记；修改下载文件不会回写兰台。

原文放入动态长度的反引号代码围栏：围栏长于正文中最长的连续反引号，因此正文里的三反引号、其他 Markdown、HTML 标签都仍处于字面文本块。下载以 `text/markdown; charset=utf-8` 和附件头返回，附 `X-Content-Type-Options: nosniff` 与 `Cache-Control: no-store`。文件名固定，避免从客户端来源标签构造路径或响应头。

隔离原文可由同一授权身份显式下载；已删除原文不导出。代码围栏只用于安全展示，不修改数据库内的原文本。

**下载的副本由下载者管理。** 兰台删除后不会自动擦除用户已经保存的 Markdown、Obsidian 同步副本、历史版本或备份。Obsidian 不是本期接收、作业与认证的权威来源。

## 固定错误与运行边界

| HTTP 状态 | 含义 |
|---|---|
| 401 | 缺少显式有效凭据或认证失败 |
| 403 | 会话 scope 不合法，或本期禁止的自报 tenant/agent 头部 |
| 404 | 功能关闭、目标未知、他人目标，或导出已删除消息 |
| 409 | 同一消息身份的业务字段发生冲突 |
| 413 | 实际请求流超过 640 KiB，与 Content-Length 声明无关 |
| 422 | 字段、时间、角色、大小或敏感内容校验失败 |
| 503 | SQLite/数据库不可用或必要表尚未迁移；不能回报保存成功 |

这些错误只返回固定消息，不回显原文、密钥、模型输入、SQL 参数或异常文本；包括 JSON/字段校验错误。错误和成功响应都不应缓存。请按既有迁移和发版流程准备数据库，MessageStore 和路由不隐式建表。

验证入口为[HTTP 测试](<../tests/test_conversation_api.py>)：真实 MessageStore、临时文件 SQLite，存储通过 dependency override 注入，不 mock 内部存储计算。合成 Principal 的三维 scope 用例只覆盖本路由明确的 `authenticate_conversation_request` 测试入口；真实认证用例移除该覆盖，使用临时 ApiKey 表调用真实 `get_current_user`。测试覆盖无效 Bearer 先拒绝且不消费坏 JSON 流、无长度或伪报长度的实际超限 `413`、最大合法 Unicode 转义载荷、同名管理身份冲突拒绝，以及功能关闭、缺表 `503`、Markdown 围栏和删除后禁止导出。具体执行成绩以本期验证日志为准。
