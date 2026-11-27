# ADR-0098：同袍工作区存储与可信身份

延续 ADR-0058 的 private / swarm_shared 两级语义。T-44（DM #1072、#1254）
在独立工作树交付工作区存储与只读挂载，不实现 T-37 的结晶、评考与晋升状态机。

真实失败用例表明，旧通用检索在调用者省略身份维度时可能放宽过滤；其写入口、
后台处理器和历史 NULL 行另有兼容契约。将敏感工作区直接写进 MemoryItem 会让
新接口的鉴权被其他入口绕过。选择在现有 SQLite 中新增 tongpao_workspace_memory
独立表，保持旧记忆管线原样；旧数据不猜测归属、不自动迁移，旧 FTS / 向量索引
不收录工作区正文。工作区查询在 SQL 中按 tenant_id / user_id 精确匹配，私有行再匹配
agent_id；公共行仅在同租户、同用户空间内跨 Agent 可见，lane 白名单也在分页之前生效。

ApiKey 增加可空 tenant_id / agent_id，迁移 v48 不回填旧密钥。新接口只接受完整绑定
的 Bearer 密钥，从库中取得身份，头部冲突即拒绝；Agent lane 配置只能收窄绑定密钥权限。
旧未绑定密钥可继续使用旧接口，无权进入新工作区。管理员可通过独立发布入口指定
目标空间写入公共典籍；普通 Agent 只有私有写入与公共读取权限。服务侧管理调用可使用
显式 system Principal，HTTP 发布入口仍严格要求 admin。

旧未绑定密钥沿用部署 Agent lane 覆盖规则；新绑定密钥使用密钥权限与部署白名单交集，
不将新策略无条件施加到旧调用。显式空 lane 授权保持为空，不回退成默认授权。

挂载返回不可变标量快照与元组，不返回 ORM 对象。工作区没有 HTTP 更新、删除或
Agent 公共发布入口，不能通过修改挂载对象改变公共知识。服务公开 store_private_memory、
publish_shared_memory 与 mount_memories，供后续晋升工单显式对接；未自动接入其状态机。

接口：POST /tongpao/private、GET /tongpao/memories、POST /tongpao/shared（管理员）。
query 是转义后的字面子串查询；limit 为 1–1000。此切片不提供语义向量检索或跨联邦共享。
默认拒绝缺失身份与非法作用域；无命中返回空数组。
