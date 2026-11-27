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

### 4. 快照筛选、分页与增量契约（2026-11-25）

- 快照接口继续只读，并沿用租户、用户、Agent 与泳道 ACL；新增 `domain`、精确 `tag`/`tags`、`theme` 子串筛选。多个标签按 AND 语义组合，`theme` 匹配正文、key 或标签文本。
- 快照使用 `(updated_at, id)` 降序 keyset 游标，不使用 offset；响应固定返回 `watermark`、`stream_cursor`、`has_more`、`next_cursor` 与 `pagination`。游标携带快照水位，后续页只读取该水位以内的当前记录；水位后的变更由增量流接续，不混入快照页。
- 节点投影包含事件时间 `event_time`、有效期 `valid_from/valid_to`、空间坐标元数据、Ebbinghaus `decay_curve` 与有界 `halo` 参数（半径上限 64）；衰减半衰期复用 `lantai.memory.decay_class.decay_multiplier`，procedural 记忆保持恒定。
- 增量接口 `GET /graph/xuanjing/changes` 首次从 `stream_cursor` 开始，后续使用响应的 `next_cursor`，按 `(created_at/updated_at, source-rank, id)` 合并排序记忆与边事件。记忆事件类型为 `memory.upsert` 或 `memory.remove`，移除事件只携带 `id`，不得泄露正文；边事件类型为 `edge.created`。
- 增量查询始终受调用方 ACL 与筛选约束；场景存在不可见成员时仅返回安全的场景 ID 与当前响应页可见计数，不泄露场景摘要、质心、热度或全局成员数。可见性判断包含已归档成员。接口响应通过 FastAPI/Pydantic 模型校验后再返回。
