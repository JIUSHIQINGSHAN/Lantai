# ADR-0059: 悬镜——3D 神经元记忆星图与拓扑数据聚合服务

**日期**: 2026-10-29  
**状态**: Accepted  
**决策者**: 大哥  
**来源**: `docs/future-roadmap.md` 方向四「悬镜」（Xuanjing）；解决记忆全貌缺乏全局可观测性、拓扑脉络与活跃衰减难以直观掌控的痛点。

---

## 背景

兰台具备因果时序拓扑（格物）、冲突账本（断金）、艾宾浩斯时效衰减（遗忘）与两级记忆联邦（同袍）。然而：
1. 现有 `GET /graph` 端点主要用于静态关系连线与基础场景聚类，缺少节点活跃度权重（importance、use_count、helpful_count）与衰减时效（decay_score）的深层数据聚合；
2. 缺乏针对 3D WebGL / Force-directed 图形引擎定制的星系聚簇（Galaxy / Cluster）与多维拓扑结构；
3. 需要为控制台提供兼顾租户隔离、权限收窄与高效只读聚合的专用拓扑服务。

---

## 决策

构建**「悬镜」（Xuanjing）**记忆星图拓扑数据聚合服务：

### 1. 架构与拓扑数据模型

在 `lantai/services/xuanjing_service.py` 落地专用聚合引擎：
- **节点（Nodes）**：
  - 提取活跃的 `MemoryItem`（过滤已被删除或完全退役的记忆）；
  - 计算节点多维渲染参数：
    - `decay_score`（0.0 ~ 1.0）：映射为恒星亮度与光晕半径；
    - `weight` / `importance`：结合 `use_count` 与 `helpful_count` 计算节点物理质量与引力；
    - `lifecycle_status` / `role` / `lane`：映射为节点色彩与纹理分类；
    - `shared_scope`：标识是否为「同袍」公共知识殿堂节点。
- **连线（Edges）**：
  - 提取 `MemoryEdge`，完整覆盖 `causes`, `triggered_by`, `leads_to`, `supports`, `refines`, `contradicts`, `supersedes`；
  - 连线携带 `confidence`、`relation` 与 `provenance`。
- **星系聚簇（Clusters / Galaxies）**：
  - 聚合 `MemoryScene` 与 `lane` 分轨，计算每个聚簇的节点计数与引力质心（centroid）。

### 2. 安全与隔离机制（宁 miss 不脏写）

- 严格遵循租户（tenant_id）与用户（user_id）行级安全隔离；
- 支持根据 `agent_id` 自动放行该 Agent 的私有记忆与属于公共殿堂的 `swarm_shared` 共享记忆；
- 对未授权端点与孤立噪点实行过滤保护。

### 3. API 与工具暴露

- 暴露 HTTP 路由：`GET /graph/xuanjing`（支持 `scene_id`、`lane`、`limit`、`include_archived` 参数）；
- 暴露 MCP 工具：`get_xuanjing_graph`。
