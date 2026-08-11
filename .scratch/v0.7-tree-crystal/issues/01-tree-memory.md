# 01 记忆分类树（TreeMemory 窄版）

借鉴源：aiduMEI `ducky/tree_memory.py`（v17.0 Mímir 风格节点树）。

## 设计

- `MemoryNode` 表：id / parent_id / name / node_path(unique) / depth / description /
  namespace / created_at；`memoryitem.tree_path TEXT`（v9 迁移）。
- 路径语义：`/projects/release` 风格；`build_node_path` + `validate_node_name`
  纯函数；父节点不存在、同级重名、name 含路径分隔符 → ValueError（宁 miss 不脏写）。
- 挂载统计：memoryitem.tree_path 精确匹配 = direct_count，前缀 LIKE = subtree_count
  （纯函数 `count_by_prefix` 可单测，避免 SQL 脆断）。
- REST：`GET /tree`、`POST /tree/nodes`、`GET /tree/subtree`、`POST /tree/assign`、
  `POST /tree/unassign`；MCP：`tree_view` / `tree_add` / `tree_assign`。

## 测试

`tests/test_tree_service.py`：纯函数（node_name 校验/路径拼接/前缀计数）+ 真实
SQLite 直调（add/subtree/assign/重名拒绝/父缺失拒绝），不 mock 内部逻辑。

## 状态

resolved（2026-08-12，随 feat(absorb) 提交推送）。
