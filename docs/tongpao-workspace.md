# 同袍私有工作区与公共典籍

工作区使用独立 SQLite 表，不自动复制历史记忆。私有内容属于租户、用户和 Agent，
公共典籍只在同一租户、同一用户空间内共享，其他 Agent 获得只读挂载。

## 管理端准备

在受信任的服务管理代码中，使用 `create_api_key` 创建带完整身份绑定的密钥并持久化：

```python
from lantai.core.auth import create_api_key
from lantai.storage.db import get_session

raw_key, key = create_api_key("user-1", ["general"], tenant_id="tenant-1", agent_id="agent-a")
with get_session() as session:
    session.add(key)
    session.commit()
```

`raw_key` 通过已有的安全凭据分发渠道交给对应 Agent，不写入日志、文档或仓库。
此功能不自动生成、分发或修改宿主密钥。旧密钥默认不授予工作区权限。

## Agent 接口

使用 `Authorization: Bearer <绑定密钥>`。身份由密钥确定，无需传租户或 Agent 头，
冲突头返回 403，未登录返回 401。请求体禁止额外身份字段。

- `POST /tongpao/private`：正文 `{"content":"任务中间状态","lane":"general"}`，保存私有记忆。
- `GET /tongpao/memories`：返回 `{"memories":[...]}`，包含自身私有记忆和公共典籍。
- `shared_scope=private` 或 `swarm_shared`：只挂载选定层；默认 `all`。
- `query`：字面子串过滤，百分号和下划线不会变成通配符；`limit` 默认 100，上限 1000。

返回字段为 id、content、shared_scope、tenant_id、user_id、agent_id、lane、created_at。
lane 权限按密钥与部署端 Agent 白名单的交集收窄；共享不会扩大 lane 权限。

## 发布公共典籍

管理员使用环境密钥 `X-API-Key` 调用 `POST /tongpao/shared`，正文包含 content、lane、
tenant_id、user_id、agent_id。普通 Agent 调用该接口返回 403。无公共更新或删除接口。
受信任的晋升代码可调用 `publish_shared_memory`，但评考流程由其他工单实现。

迁移 v48 新建空工作区表并为旧密钥新增空身份列，历史密钥、记忆和归属不被改写。
此切片仅在开发分支准备，未部署或对真实库执行迁移。更多取舍见
[ADR-0098](adr/0098-tongpao-workspace-storage.md)。
