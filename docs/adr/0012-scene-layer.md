# ADR-0012: scene 聚合层——渐进式披露的导航注入

**日期**: 2026-08-11
**状态**: Accepted
**决策者**: 大哥
**来源**: [票据 01](../../.scratch/scene-layer/issues/01-scene-layer-adr.md)

## 背景

腾讯 TencentDB Agent Memory 的 L2 场景层（`MemoryCore/src/core/scene/`）把记忆按场景聚合：
LLM 生成 scene_blocks/*.md（meta 含 summary/heat/created/updated），`scene_index.json` 维护索引，
召回时注入「场景导航」（按 heat 排序的 Path + Summary），Agent 按需用 read 工具下钻全文——
渐进式披露，避免一次注入撑爆上下文。

兰台现状：`hybrid_search` 把 top_k 条命中记忆平铺注入（`scripts/shell_hook.py::build_context`，
单条/总字符双预算）。记忆量大时有两个问题：
1. **跨场景上下文缺失**——top_k 只给最像的几条，同一主题散落多条时互相割裂；
2. **注入体积不可控**——全量场景细节撑爆预算，只能靠截断。

调研见 `docs/research/tencentdb-agent-memory-borrow.md`。

## 决策

| 项 | 决策 |
|----|------|
| 数据模型 | 新增 `MemoryScene` 表（id/name/summary/heat/member_count/created_at/updated_at）+ `MemoryItem.scene_id`（可空，多对一，index） |
| Schema 迁移 | 复用 `lantai/storage/db.py` 迁移链：`CURRENT_SCHEMA_VERSION` 2 → 3（`_ensure_column(memoryitem, scene_id)` + 建 `memoryscene` 表），老库无损升级 |
| 场景构建 | 写入侧确定性优先：embedding 余弦聚类成簇（复用现有向量），LLM 仅批量命名/摘要（失败用代表 key 兜底，宁 miss 不脏写）；`POST /scenes/rebuild` 手动重建入口 |
| heat | 场景内成员 `use_count` 求和（零写放大，复用既有字段），排序用 heat + member_count 双键 |
| 导航注入 | `scene_navigation(results, budget)` 纯函数：命中记忆按 scene 分组 → `## Scene: 名称（热度 N）` + 摘要 + 成员 key 列表；复用 `_apply_recall_budget` 预算 |
| 渐进式披露 | shell_hook 注入场景导航优先、详情条目同一预算内从属；MCP 新工具 `scene_get(scene_id)` 下钻全场景 |
| 可观测 | （后续）RetrievalEvent 记录 scene_id 命中与零场景召回监控并入「可观测性」项，不在本实现 |
| 兼容开关 | `SCENE_LAYER_ENABLED`（默认关，rebuild 后开）；无 scene_id 的记忆保持现状平铺 |

## 理由

- 复用既有 embedding、预算机制、迁移链，零破坏；新表 + 新列不触碰现有读写路径
- `scene_navigation` 是纯函数，符合测试纪律（不 mock 冒烟测试）
- 渐进式披露与召回预算天然兼容：导航行也是字符串，走同一预算入口
- 不照搬腾讯文件系统方案（scene_blocks/*.md）：兰台记忆在 SQLite，文件化会引入一致性/备份问题
- 场景构建先手动 `POST /scenes/rebuild`（幂等全量重建）验证聚类质量，增量聚类后置（先确定性后自动化）

## 后续（不在本 ADR）

- heat 驱动的场景浮顶/衰减、场景综述持续维护（对应腾讯 LLM-Wiki）
- 场景级 ACL（结合多 Agent 资产绑定）
- 场景构建自动化：消化期增量聚类（先手动 rebuild 验证质量）
