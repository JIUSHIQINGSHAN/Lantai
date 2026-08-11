# 01 - scene 聚合层 ADR（渐进式披露导航注入）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory L2 场景层，给兰台加「场景聚合 + 导航注入 + 渐进式披露」：
命中记忆按场景分组注入导航（场景名 + 摘要 + 成员 key），需要详情时用 `scene_get` 下钻。
解决单条平铺注入在记忆量大时的跨场景上下文缺失与注入体积不可控。

## 范围（本票只评审设计，不实现）

- 数据模型：`MemoryScene` 表 + `MemoryItem.scene_id`
- 迁移：`user_version` 2 → 3（幂等，老库零丢失）
- 构建：embedding 聚类 + LLM 命名/摘要（可降级），`POST /scenes/rebuild`
- 检索：`scene_navigation` 纯函数 + shell_hook 导航注入 + MCP `scene_get`
- 开关与可观测：`SCENE_LAYER_ENABLED`、RetrievalEvent.scene_id

## 验收（评审通过后实现时）

1. `scene_navigation` 纯函数有不 mock 冒烟测试
2. 迁移 v2→v3 幂等、老库数据零丢失（复用 tests/test_migrations.py 模式）
3. shell_hook 有场景时注入导航段、无场景时行为不变（回归）
4. 导航行受单条/总预算约束（复用 `_apply_recall_budget`）
5. 全量 pytest 绿

## 相关文件

docs/adr/0012-scene-layer.md（本票产物）、lantai/retrieval/hybrid.py、
scripts/shell_hook.py、lantai/models/tables.py、lantai/storage/db.py、
docs/research/tencentdb-agent-memory-borrow.md

## Comments

## Answer（2026-08-11 评审通过，三条决策已定）

1. 数据模型：独立 `MemoryScene` 表（tags 装不下 summary/heat/生命周期，场景是聚合实体）
2. heat：成员 `use_count` 求和（零写放大），排序用 heat + member_count 双键；RetrievalEvent.scene_id 并入可观测性项
3. 场景构建：先手动 `POST /scenes/rebuild`（幂等全量重建），增量聚类后置

## 实现完成（2026-08-11，全量 pytest 518 passed）

- 数据模型：`MemoryScene` 表 + `MemoryItem.scene_id`；迁移 `CURRENT_SCHEMA_VERSION` 2→3（`_ensure_column` + 建表 + 索引，空库跳过索引不炸）
- 纯函数（不 mock 冒烟）：`cosine_sim` / `cluster_scenes`（贪心质心聚类）/ `pick_representative` / `format_scene_block` / `scene_navigation`
- 写入侧：`rebuild_scenes`（幂等全量重建，单成员簇不建场景，heat = 成员 use_count 求和，LLM 命名失败降级代表 key）；`POST /scenes/rebuild`
- 读取侧：shell_hook `build_context` 场景导航块优先注入（`## Scene: 名称（热度 N，成员 M）` + 摘要 + 成员 key），渐进式披露；MCP `scene_get` / `scenes_list`；REST `GET /scenes` / `GET /scenes/{id}`
- 共享文本工具提取到 `lantai/core/text.py`（截断 + 总预算），shell_hook 别名兼容
- 测试：`tests/test_scene.py` 11 例（纯函数 6 + 迁移 1 + 写入 2 + 读取 2），`test_migrations.py` 适配 v3，`test_mcp.py` 工具数 14
- 文档：CHANGELOG 条目、CONTEXT 词汇表「scene 场景聚合」、ADR-0012 Accepted

## 增量聚类完成（2026-08-11，全量 pytest 通过）

- 数据模型：`MemoryScene.centroid` 质心落库（`user_version` 4→5 增量迁移，`_ensure_column` 幂等）；`rebuild_scenes` 构建时同步落质心（`_mean_vector` 纯函数）
- 纯函数（不 mock 冒烟）：`incremental_cluster`（复用 `cosine_sim`，cosine ≥ 阈值并入最相似场景，未命中返回 None——宁 miss 不脏写）
- 写入侧：`assign_new_memory`（embed 新记忆 → 与既有场景质心比较 → 命中写 scene_id 并刷 heat/member_count，零写放大）；`assign_unassigned`（扫描 `scene_id IS NULL` 的 active 记忆逐条归属）
- 挂钩：`run_evolve_once` 消化期末尾自动补跑（`SCENE_LAYER_ENABLED` 门控，异常不影响演化）+ REST `POST /scenes/assign` 手动入口
- 测试：`test_scene.py` 新增 5 例（纯函数冒烟 1 + assign 集成 3 + v4→v5 迁移 1），rebuild 测试补质心断言；`test_observability.py` 迁移断言同步 `CURRENT_SCHEMA_VERSION`；`test_mcp.py` 工具数同步 15