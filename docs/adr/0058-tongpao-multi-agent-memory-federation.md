# ADR-0058: 同袍——多智能体记忆联邦与公共知识殿堂

**日期**: 2026-10-29  
**状态**: Accepted  
**决策者**: 大哥  
**来源**: `docs/future-roadmap.md` 方向三「同袍」（Tongpao）；解决多智能体协同（Swarm / Agent Teams）中私有记忆与公共知识缺乏分级共享的痛点。

---

## 背景

兰台在 v0.22 确立了归属四元组（`tenant_id`, `user_id`, `agent_id`, `session_id`），在 v0.34 落地了「辨域」（`domain`: user / session / agent 三维硬隔离）。
但在多智能体集群与 Swarm 协作场景中，存在如下矛盾：
1. **私有壁垒过硬**：Agent A 在特定任务中探索出的环境知识或操作法门，被封闭在自身的私有工作区中，其他协作 Agent 无法获知；
2. **缺乏公共殿堂**：缺少一个全体或同群组 Agent 均可信任、默认可见、受治理的公共知识空间（Swarm Shared Knowledge）；
3. **技能流转断层**：单个 Agent 验证有效的高频技能结晶（`SkillCrystal`）无法安全晋升成为共享公共技能资产。

---

## 决策

引入**「同袍」（Tongpao）** 多智能体记忆联邦机制：

### 1. 概念与两级存储架构

- **私有工作区（Agent Private Memory）**：
  - `shared_scope = "private"`（默认）；
  - 严格绑定具体的 `agent_id` 与 `user_id`，遵循原有行级 ACL 隔离，仅所属主体可查。
- **公共知识殿堂（Swarm Shared Knowledge）**：
  - `shared_scope = "swarm_shared"`；
  - 存放集群公共规约、已验证共享知识；在同一租户或用户空间内，所有协作 Agent 默认可见与召回；
  - 也可通过 `domain = "federated"` 进行显式域隔离检索。

### 2. 模型与元数据扩展

- `MemoryItem` 补充 `shared_scope: str = Field(default="private", index=True)`；
- 兼容旧数据：既有历史数据视作 `private`，宁 miss 不脏写；
- 数据库增量迁移升级为 `v35`（`storage/migrations.py` 补充 `_migrate_v35`，TARGET_SCHEMA_VERSION=35）。

### 3. ACL 与检索放行机制（宁 miss 不脏写）

- 读侧收窄与放行逻辑：
  - `admin` / `SYSTEM_VIEWER`：全权放行；
  - 普通 Agent 调用：
    - 允许召回属于自身私有的记忆：`(m.agent_id == viewer_agent OR m.agent_id IS NULL)`；
    - **并且**放行同租户/用户下的公共共享知识：`m.shared_scope == "swarm_shared"`；
    - 异租户公共记忆仍被租户边界阻断。

---

## 理由

1. **典籍出处**：《诗经·秦风·无衣》“岂曰无衣？与子同袍”，取协同作战、资源共享之意。
2. **渐进式演进**：向后兼容既有 `MemoryItem` 读写契约，未指定 `shared_scope` 时保持默认私有安全。
3. **严密安全防线**：共享记忆仅在合法用户与租户边界内放行，不破坏多租户行级隔离。

---

## 影响

- 数据库：升级 `CURRENT_SCHEMA_VERSION = 35`，`memoryitem` 增补 `shared_scope` 列与索引。
- 检索层：`hybrid_search`、`search_fts`、`vector_owner_filter` 支持 `swarm_shared` 穿透放行。
- 接口层：REST `/search`、`/add` 及 MCP 工具支持 `shared_scope` 设定与过滤。
- 测试：`tests/test_tongpao_federation_storage_smoke.py` 真实端到端不 mock 冒烟。

## T-37 实现细化（2026-11-25）

为区分同租户、同用户下的协作组，在既有两级共享范围外增加
`federation_id`，保留 `private` 与 `swarm_shared` 的含义。

- 联邦成员资格由部署配置 `AGENT_FEDERATION_BINDINGS` 绑定。优先使用
  `tenant_id|user_id|agent_id` 键，兼容显式 Agent 键；调用方不能自行指定未绑定
  或不匹配的联邦。普通 REST Bearer 身份以密钥绑定的 Agent 为准。
- 同袍引擎写入要求明确的租户、用户、Agent、联邦四项身份；普通读取只能看到
  自己的私有记忆和同租户、同用户、同联邦的公共记忆。旧行的空联邦保持既有
  归属边界兼容；缺少联邦身份的读取不放行已标联邦的行。管理身份保留原有权限。
- MCP `search` 与四个结晶工具广告并消费租户、Agent、联邦参数，统一经过可信
  绑定解析；普通 `user_id` 单字段调用保持兼容。拒绝结晶缺少 `reason` 时先在
  MCP 参数入口返回 `-32602`，无身份原始调用也遵循此参数契约，且不执行写入。
- 提取草稿先进入私有观察，不把输入的成功统计当作验证。影子执行只允许当前
  主体登记自身 Agent 的结果，同联邦同用户的协作者可参与复现但不获私有读取权。
  评考默认要求至少 3 次成功、2 个不同 Agent 参与；步骤为空拒绝，未达标延后。
- 评考写入 `SkillPromotionAudit`，记录计数、步骤哈希和理由；发布复用
  `create_skill`，按完整归属与共享范围幂等去重，再写 `leads_to` 边及
  `SwarmKnowledgeEvent` 游标。重复完成的晋升返回既有记忆及事件。
  技能创建和发布流痕分次提交，尚非跨 SQLite、FTS、向量索引的原子事务；
  中途失败不返回成功，重试复用既有技能补完流痕。此处不承诺故障窗口内原子可见。
- 存储迁移 v48 补联邦归属、影子评考字段、审核表、事件表及 API Key 的 Agent
  绑定；v49 将结晶名称的全局唯一约束改为租户、联邦、用户、Agent、共享范围
  与名称的复合唯一，保留已有行，不猜填历史归属。v40 索引对齐须识别表达式
  涉及的列，缺列时留待后续版本补齐，避免提前创建索引中断老库升级。

验证入口为三个 MCP/默认值契约文件、身份收敛 wave4 与 `test_tongpao*` 系列；
全量验收须带覆盖率和宿主库绊线，老库回放在隔离目录执行。
