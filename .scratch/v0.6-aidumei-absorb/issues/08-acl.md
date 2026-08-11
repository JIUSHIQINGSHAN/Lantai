# 08 - 资产绑定 + lane 级 ACL（按 agent_id 绑定 lane 集）

Status: resolved
Type: task
Blocked by: (none)

## 目标

借鉴 TencentDB Agent Memory Memory Hub Fixed Binding + ACL 四级收窄的窄版落点：
给兰台加「按 agent_id 绑定 lane 集」的访问收窄——绑定过的 agent 只能检索/写入
自己 lane 集内的记忆。默认关闭（AGENT_LANE_BINDINGS 为空 = 现状行为），
零硬编码（配置驱动），不改变任何既有调用路径。

## 范围

- `lantai/core/acl.py`（新）：
  - `allowed_lanes(agent_id)` 纯函数——绑定表查 agent 允许的 lane 集；
    未启用返回 None（不受限）。
  - `lane_allowed(agent_id, lane)` 纯函数——写入侧校验。
  - `verify_agent` FastAPI 依赖——ACL 启用时强制要求 X-Agent-Id 且已绑定
    （缺失/未绑定 → 403）；未启用放行。
- `lantai/core/settings.py`：`AGENT_LANE_BINDINGS: dict[str, list[str]] = {}`
  （agent_id → lane 白名单；空 = 不启用）。
- 检索收窄：`POST /search` 注入 verify_agent，返回结果按允许 lane 集过滤
  （宁 miss 不脏写——不放行未绑定 lane 的记忆）。
- 写入收窄：`POST /add`、`POST /add/raw` 注入 verify_agent，lane 不在绑定集
  → 403（不落库）。
- 全局保护：protected_routers 统一挂 `Depends(verify_agent)`（ACL 关闭时零开销）。
- 文档：spec.md、CHANGELOG、CONTEXT 词汇表（ACL）、tencentdb 报告表行更新。
  MCP 工具直连 service 不经 REST——MCP 侧 agent_id 传递记为后续项。

## 验收

1. `allowed_lanes` / `lane_allowed` 不 mock 冒烟：未启用/绑定/未绑定 三态。
2. REST：ACL 启用时缺 X-Agent-Id → 403；绑定 agent 写越界 lane → 403；
   search 结果只含绑定 lane（真实 SQLite + TestClient）。
3. ACL 关闭（默认）全量测试无回归（既有调用路径零变化）。
4. 全量测试无回归。

## 相关文件

lantai/core/acl.py（新）、lantai/core/settings.py、lantai/api/routes_search.py、
lantai/api/routes_memory.py、api_server.py、tests/test_acl.py（新）、
docs/research/tencentdb-agent-memory-borrow.md


## Answer（2026-08-11 已实现，test_acl.py 7/7 + 全量无回归）

实现内容：
- `lantai/core/acl.py`：`allowed_lanes`（未启用 None / 绑定集 / 未绑定空集）、
  `lane_allowed`（写入校验）、`filter_results_by_lanes`（兼容 memory.lane 与
  FTS 兜底无 lane 两形态，无 lane 视为 general，宁 miss 不放行）、
  `verify_agent` 依赖（ACL 启用时强制 X-Agent-Id 且已绑定，否则 403）。
- settings `AGENT_LANE_BINDINGS: dict[str, list[str]] = {}`（空 = 不启用）。
- api_server：protected_routers 统一挂 `Depends(verify_agent)`（默认关闭零开销）。
- routes_search：注入 agent_id，结果在返回前按绑定 lane 过滤（_try_log 前，
  观测与响应一致）；routes_memory：/add、/add/raw 注入 agent_id，越界 lane → 403。
- 测试：纯函数 3 例（三态 + 两形态过滤）+ 路由 4 例（默认关闭回归、缺 header/
  未绑定 403、写入越界 403、检索收窄接线）。
- MCP 工具直连 service 不经 REST，agent 身份传递记为后续项。

验收对照：
1. ✅ allowed_lanes / lane_allowed / filter_results_by_lanes 不 mock 三态
2. ✅ 缺 X-Agent-Id → 403；越界 lane 写入 → 403；search 只含绑定 lane
3. ✅ 默认关闭全量无回归（既有调用路径零变化）
4. ✅ 全量测试无回归
