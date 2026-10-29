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
