# 01 MAP 星图面板（零依赖力导向 + /graph 数据服务）

借鉴源：aiduMEI v18.3.0 MAP 面板（ECharts 力导向知识域星图）。

状态：resolved（2026-08-12，随提交推送）

## 设计

- `lantai/ops/graph.py::build_graph(session, limit=150)` 纯函数：
  节点 = active 记忆（label/lane/decay_class/scene_id）+ 参与边或属 scene 才入选；
  链接 = MemoryEdge（source/target/relation/confidence，两端均存在才保留）；
  返回 {nodes, links, stats{lane_counts, edge_counts}}。纯函数零 DB 零 LLM。
- REST `GET /graph`（受保护）；MCP `graph_view`（只读）。
- `/ui/map`：内联 HTML + SVG 放射布局——6 个 lane 扇区，scene 成员同色聚簇，
  边按 relation 配色（supports 绿 / refines 蓝 / contradicts 橙 / supersedes 红），
  hover 显示 label/relation，点击节点跳 `/ui/vault` 检索。零外部依赖。

## 测试

`tests/test_graph.py`：纯函数（节点入选规则/边过滤/统计）+ 真实 SQLite 直调
（有边记忆入选、孤立记忆不入选、supersedes 链保留）。

## 实现记录

- `lantai/ops/graph.py::build_graph(session, limit=150)`：候选池 = active 记忆按
  updated_at 降序取 limit 条；节点 = 参与入选边任一端或携带 scene_id 的记忆 +
  参与边的来源文档 RawDocument（doc_*，node_type=source，带 title/url）；边 = 两端
  均在入选集合才保留（跨池边、指向 archived/池外端点丢弃）；返回
  nodes/links/scenes/stats（node_type_counts + lane_counts + edge_counts）。
  真实库验证：25 active 记忆 + 25 条 doc->mem 支撑边 → 40 节点
  （23 记忆 + 17 来源）/ 23 边。
- REST `GET /graph`（受保护，limit∈[1,500] 越界 422）；MCP `graph_view`（38→39，
  同校验）；`/ui/map` 零依赖内联 SVG 放射布局（6 lane 扇区 + scene 橙色虚环聚簇 +
  来源文档外环矩形贴邻接记忆 + 悬停详情 + 点击记忆跳档案 / 来源开 URL）；`/ui`
  首页第五面板卡片。
- 测试：`tests/test_graph.py` 8 例（纯函数 + 真实 SQLite，不 mock 内部逻辑，含来源
  文档节点入选 / doc->archived 边丢弃）；`tests/test_mcp.py` 工具数 39 +
  `graph_view` roundtrip + limit 越界 2 例。
- 明确不吸收：layer1_selfcheck 自动合并（违宁 miss 不脏写）；instinct_graduation
  自动毕业删原文（v0.7 crystal 已覆盖）。

## code-review 收口（2026-08-12）

- scene 成员同色聚簇落地（场景调色板，solo 仍按 lane 色）——spec 原文语义兑现。
- 点击记忆节点跳 `/ui/recall?q=label`（recall 页新增 `?q=` 预填自动检索）——
  「点击跳档案检索」语义落地。
- 修复 `_MAP_HTML` 未定义 `info` 变量（悬停详情 ReferenceError 硬 bug）、
  renderStats 字符串拼接优先级（兜底永不触发）、死代码 `Math.min(13,11)`。
- limit 校验提取 `ops/graph.validate_graph_limit`（REST/MCP/纯函数三处共用），
  build_graph 非法 limit 抛 ValueError 不静默钳制（宁 miss 不脏写）；
  `test_graph.py` 新增非法 limit 用例（9 例）。
